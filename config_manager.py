import os
import json
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session

# Setup Logging
logger = logging.getLogger("DB_Replicator.Config")

# SQLAlchemy Base
Base = declarative_base()

# --- Models ---

class Project(Base):
    __tablename__ = 'projects'
    
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String, nullable=True)
    # Name Source Configuration
    name_source_type = Column(String, default="DEFAULT") # DEFAULT, DB, FILE
    name_source_value = Column(String, nullable=True)    # "Table.Column" or Path
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    tables = relationship("ProjectTable", back_populates="project", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Project(name='{self.name}')>"

class ProjectTable(Base):
    __tablename__ = 'project_tables'
    
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False)
    table_name = Column(String, nullable=False)
    is_selected = Column(Boolean, default=False)
    filter_clause = Column(String, nullable=True) # SQL WHERE clause
    
    project = relationship("Project", back_populates="tables")
    sensitive_columns = relationship("SensitiveColumn", back_populates="table_config", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint('project_id', 'table_name', name='uq_project_table'),)

class SensitiveColumn(Base):
    __tablename__ = 'sensitive_columns'
    
    id = Column(Integer, primary_key=True)
    project_table_id = Column(Integer, ForeignKey('project_tables.id'), nullable=False)
    column_name = Column(String, nullable=False)
    function_name = Column(String, nullable=False) # e.g. obfuscate_name
    seed_column = Column(String, nullable=True)
    
    table_config = relationship("ProjectTable", back_populates="sensitive_columns")

    __table_args__ = (UniqueConstraint('project_table_id', 'column_name', name='uq_table_column'),)


# --- Config Manager ---

class ConfigManager:
    DB_FILE = "config.db"

    def __init__(self, db_path=None):
        if db_path:
            self.DB_FILE = db_path
            
        self.engine = create_engine(f"sqlite:///{self.DB_FILE}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def get_session(self) -> Session:
        return self.Session()

    def get_all_projects(self) -> List[Project]:
        """Return list of all projects"""
        with self.get_session() as session:
            return session.query(Project).all()

    def get_project_by_id(self, project_id: int) -> Optional[Project]:
        with self.get_session() as session:
            return session.get(Project, project_id)
            
    def get_project_by_name(self, name: str) -> Optional[Project]:
        with self.get_session() as session:
            return session.query(Project).filter_by(name=name).first()

    def create_project(self, name: str, description: str = "", name_source_type="DEFAULT", name_source_value=None) -> Project:
        """Create a new project"""
        with self.get_session() as session:
            try:
                project = Project(
                    name=name, 
                    description=description,
                    name_source_type=name_source_type,
                    name_source_value=name_source_value
                )
                session.add(project)
                session.commit()
                # Refresh to get ID
                session.refresh(project)
                # Detach from session so it can be used outside
                session.expunge(project)
                return project
            except Exception as e:
                session.rollback()
                logger.error(f"Failed to create project: {e}")
                raise

    def delete_project(self, project_id: int):
        with self.get_session() as session:
            project = session.get(Project, project_id)
            if project:
                session.delete(project)
                session.commit()

    def get_project_config(self, project_id: int):
        """
        Get full configuration for a project.
        Returns tuple: (selected_tables_set, filters_dict, sensitive_columns_dict, name_source_config)
        """
        with self.get_session() as session:
            project = session.get(Project, project_id)
            if not project:
                return set(), {}, {}, {}

            selected_tables = set()
            filters = {}
            sensitive_columns = {}
            
            # Eager load could be optimized, but for config usage simple iteration is fine
            for pt in project.tables:
                if pt.is_selected:
                    selected_tables.add(pt.table_name)
                
                if pt.filter_clause:
                    filters[pt.table_name] = pt.filter_clause
                
                sc_map = {}
                for sc in pt.sensitive_columns:
                    sc_map[sc.column_name] = (sc.function_name, sc.seed_column)
                
                if sc_map:
                    sensitive_columns[pt.table_name] = sc_map
            
            name_source_config = {
                'type': project.name_source_type,
                'value': project.name_source_value
            }

            return selected_tables, filters, sensitive_columns, name_source_config

    def save_project_state(self, project_id: int, 
                          selected_tables: List[str], 
                          filters: Dict[str, str], 
                          sensitive_columns: Dict[str, Dict[str, Tuple[str, str]]]):
        """
        Save the entire state of a project's table configurations.
        Includes selection status, filters, and PII rules.
        """
        with self.get_session() as session:
            try:
                project = session.get(Project, project_id)
                if not project:
                    raise ValueError(f"Project ID {project_id} not found")

                # Map existing ProjectTables
                existing_tables = {pt.table_name: pt for pt in project.tables}
                
                # Consolidate all relevant tables (selected, or has filter, or has PII)
                all_involved_tables = set(selected_tables) | set(filters.keys()) | set(sensitive_columns.keys())
                
                # We need to process existing tables to update them, and create new ones
                # Ideally, we should also handle "unselecting" tables. 
                # Simplest approach: Update/Create for involved tables, Mark others as not selected.
                
                timestamp = datetime.now()
                project.updated_at = timestamp

                for table_name in all_involved_tables:
                    pt = existing_tables.get(table_name)
                    if not pt:
                        pt = ProjectTable(project_id=project_id, table_name=table_name)
                        session.add(pt)
                    
                    # Update selection
                    pt.is_selected = (table_name in selected_tables)
                    
                    # Update Filter
                    pt.filter_clause = filters.get(table_name)
                    
                    # Update Sensitive Columns
                    # First clear existing (lazy way, or check diff)
                    # For simplicity in this tool: clear and re-add for this table
                    if pt.id: # If it's an existing record
                        session.query(SensitiveColumn).filter_by(project_table_id=pt.id).delete()
                    
                    # Re-add
                    rules = sensitive_columns.get(table_name, {})
                    for col, (func_name, seed) in rules.items():
                        sc = SensitiveColumn(
                            table_config=pt, # Link to object
                            column_name=col,
                            function_name=func_name,
                            seed_column=seed
                        )
                        session.add(sc)
                
                # For tables that existed but are no longer in "all_involved_tables"
                # (e.g. unselected and filters removed)
                # We can either delete them or just set is_selected=False
                for table_name, pt in existing_tables.items():
                    if table_name not in all_involved_tables:
                        pt.is_selected = False
                        # We keep filters/PII even if unselected? 
                        # Usage requirement: "persist these info". 
                        # If I uncheck a table, I probably want the filter to remain if I check it back later.
                        # So we do NOTHING else for these.
                
                session.commit()
                logger.info(f"Saved project state for project {project_id}")

            except Exception as e:
                session.rollback()
                logger.error(f"Failed to save project state: {e}")
                raise

    def migrate_json_if_needed(self):
        """Convert legacy JSON files to Default Project if DB is empty"""
        # Check if DB is empty
        projects = self.get_all_projects()
        if projects:
            return # Already has data
        
        logger.info("Checking for legacy JSON files to migrate...")
        
        json_filters = {}
        json_sensitive = {}
        
        # Load legacy files
        if os.path.exists('large_table_filters.json'):
            try:
                with open('large_table_filters.json', 'r', encoding='utf-8') as f:
                    json_filters = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load legacy filters: {e}")

        if os.path.exists('sensitive_columns.json'):
            try:
                with open('sensitive_columns.json', 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Convert list to tuple
                    for table, rules in data.items():
                        json_sensitive[table] = {}
                        for col, val in rules.items():
                            if isinstance(val, list) and len(val) >= 2:
                                json_sensitive[table][col] = (val[0], val[1])
            except Exception as e:
                logger.warning(f"Failed to load legacy PII rules: {e}")

        if not json_filters and not json_sensitive:
            # Nothing to migrate, just create a default project
            self.create_default_project()
            return

        logger.info("Migrating legacy JSON config to 'Default' project...")
        project = self.create_project("Default", "Migrated from legacy JSON files")
        
        # Since we don't know "selected" tables from JSONs (previously user had to select every time),
        # we will assume all tables referenced in filters/PII are "interesting" but maybe not selected by default?
        # OR we just leave them unselected.
        selected_tables = [] # Start empty, let user select
        
        self.save_project_state(project.id, selected_tables, json_filters, json_sensitive)
        logger.info("Migration complete.")

    def create_default_project(self):
        self.create_project("Default", "Default Project")

    def update_project_settings(self, project_id: int, name: str, description: str, 
                              name_source_type: str, name_source_value: str):
        with self.get_session() as session:
            project = session.get(Project, project_id)
            if project:
                project.name = name
                project.description = description
                project.name_source_type = name_source_type
                project.name_source_value = name_source_value
                session.commit()

    def clone_project(self, source_project_id: int, new_name: str) -> Project:
        """Clone an existing project with all its table configs and PII rules"""
        with self.get_session() as session:
            source = session.get(Project, source_project_id)
            if not source:
                raise ValueError(f"Source project ID {source_project_id} not found")

            # Create new project with same settings
            new_project = Project(
                name=new_name,
                description=f"Cloned from [{source.name}]",
                name_source_type=source.name_source_type,
                name_source_value=source.name_source_value
            )
            session.add(new_project)
            session.flush()  # Get new_project.id

            # Clone all table configs
            for pt in source.tables:
                new_pt = ProjectTable(
                    project_id=new_project.id,
                    table_name=pt.table_name,
                    is_selected=pt.is_selected,
                    filter_clause=pt.filter_clause
                )
                session.add(new_pt)
                session.flush()  # Get new_pt.id

                # Clone sensitive columns
                for sc in pt.sensitive_columns:
                    new_sc = SensitiveColumn(
                        project_table_id=new_pt.id,
                        column_name=sc.column_name,
                        function_name=sc.function_name,
                        seed_column=sc.seed_column
                    )
                    session.add(new_sc)

            session.commit()
            session.refresh(new_project)
            session.expunge(new_project)
            return new_project

    def export_to_json(self, project_id: int, output_dir: str = ".") -> Tuple[str, str]:
        """
        Export a project's filters and sensitive columns to JSON files.
        Returns tuple of (filters_path, sensitive_path).
        """
        with self.get_session() as session:
            project = session.get(Project, project_id)
            if not project:
                raise ValueError(f"Project ID {project_id} not found")

            filters = {}
            sensitive_columns = {}

            for pt in project.tables:
                if pt.filter_clause:
                    filters[pt.table_name] = pt.filter_clause

                sc_map = {}
                for sc in pt.sensitive_columns:
                    sc_map[sc.column_name] = [sc.function_name, sc.seed_column]
                if sc_map:
                    sensitive_columns[pt.table_name] = sc_map

            safe_name = project.name.replace(" ", "_")
            filters_path = os.path.join(output_dir, f"{safe_name}_filters.json")
            sensitive_path = os.path.join(output_dir, f"{safe_name}_sensitive_columns.json")

            with open(filters_path, 'w', encoding='utf-8') as f:
                json.dump(filters, f, ensure_ascii=False, indent=2)

            with open(sensitive_path, 'w', encoding='utf-8') as f:
                json.dump(sensitive_columns, f, ensure_ascii=False, indent=2)

            logger.info(f"Exported project '{project.name}' to: {filters_path}, {sensitive_path}")
            return filters_path, sensitive_path

    def import_from_json(self, project_id: int, prefix: str, input_dir: str = "."):
        """
        Import filters and sensitive columns from JSON files into a project.
        Reads {prefix}_filters.json and {prefix}_sensitive_columns.json.
        """
        filters_path = os.path.join(input_dir, f"{prefix}_filters.json")
        sensitive_path = os.path.join(input_dir, f"{prefix}_sensitive_columns.json")

        json_filters = {}
        json_sensitive = {}

        if os.path.exists(filters_path):
            with open(filters_path, 'r', encoding='utf-8') as f:
                json_filters = json.load(f)
            logger.info(f"Loaded filters from: {filters_path}")
        else:
            logger.warning(f"Filters file not found: {filters_path}")

        if os.path.exists(sensitive_path):
            with open(sensitive_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for table, rules in data.items():
                    json_sensitive[table] = {}
                    for col, val in rules.items():
                        if isinstance(val, list) and len(val) >= 2:
                            json_sensitive[table][col] = (val[0], val[1])
            logger.info(f"Loaded sensitive columns from: {sensitive_path}")
        else:
            logger.warning(f"Sensitive columns file not found: {sensitive_path}")

        if not json_filters and not json_sensitive:
            raise FileNotFoundError(
                f"找不到設定檔：\n  {filters_path}\n  {sensitive_path}"
            )

        # Get current selected tables to preserve selection state
        selected_set, existing_filters, existing_pii, _ = self.get_project_config(project_id)

        # Merge: imported data overwrites existing
        existing_filters.update(json_filters)
        existing_pii.update(json_sensitive)

        self.save_project_state(
            project_id,
            list(selected_set),
            existing_filters,
            existing_pii
        )
        logger.info(f"Imported config into project {project_id}")

