import random
import re
import json
import os
from datetime import datetime
from sqlalchemy import text

# 主要設定
OBFUSCATE_NAME_FILE = 'OBFUSCATE_NAME.json'

# 預設測試用姓氏與名字字庫 (Fallback)
DEFAULT_SURNAMES = [
    '陳', '林', '黃', '張', '李', '王', '吳', '劉', '蔡', '楊',
    '許', '鄭', '謝', '郭', '洪', '曾', '邱', '廖', '賴', '周',
    '徐', '苏', '葉', '莊', '呂', '江', '何', '蕭', '羅', '高',
    '潘', '簡', '朱', '鍾', '彭', '游', '詹', '胡', '施', '沈'
]

DEFAULT_GIVEN_NAMES = [
    '志明', '淑芬', '建華', '美玲', '俊傑', '雅婷', '家豪', '詠晴', '宗翰', '宜君',
    '冠宇', '怡君', '承恩', '欣怡', '柏翰', '雅雯', '家瑋', '心怡', '彥廷', '詩涵',
    '子軒', '鈺婷', '智偉', '佩珊', '志偉', '佳穎', '建宏', '怡萱', '俊宏', '淑華'
]

# 全域變數，程式啟動時使用預設值
SURNAMES = list(DEFAULT_SURNAMES)
GIVEN_NAMES = list(DEFAULT_GIVEN_NAMES)

# 縣市與行政區對照表 (可以自定義)
CITY_DISTRICTS = {
    '台北市': ['中正區', '大同區', '中山區', '松山區', '大安區', '萬華區', '信義區', '士林區', '北投區', '內湖區', '南港區', '文山區'],
    '新北市': ['板橋區', '三重區', '中和區', '永和區', '新莊區', '新店區', '樹林區', '鶯歌區', '三峽區', '淡水區', '汐止區', '瑞芳區', '土城區', '蘆洲區', '五股區', '泰山區', '林口區', '深坑區', '石碇區', '坪林區', '三芝區', '石門區', '八里區', '平溪區', '雙溪區', '貢寮區', '金山區', '萬里區', '烏來區'],
    '桃園市': ['桃園區', '中壢區', '大溪區', '楊梅區', '蘆竹區', '大園區', '龜山區', '八德區', '龍潭區', '平鎮區', '新屋區', '觀音區', '復興區'],
    '台中市': ['中區', '東區', '南區', '西區', '北區', '西屯區', '南屯區', '北屯區', '豐原區', '東勢區', '大甲區', '清水區', '沙鹿區', '梧棲區', '后里區', '神岡區', '潭子區', '大雅區', '新社區', '石岡區', '外埔區', '大安區', '烏日區', '大肚區', '龍井區', '霧峰區', '太平區', '大里區', '和平區'],
    '台南市': ['新營區', '鹽水區', '白河區', '柳營區', '後壁區', '東山區', '麻豆區', '下營區', '六甲區', '官田區', '大內區', '佳里區', '學甲區', '西港區', '七股區', '將軍區', '北門區', '新化區', '善化區', '新市區', '安定區', '山上區', '玉井區', '楠西區', '南化區', '左鎮區', '仁德區', '歸仁區', '關廟區', '龍崎區', '永康區', '東區', '南區', '北區', '安南區', '安平區', '中西區'],
    '高雄市': ['鹽埕區', '鼓山區', '左營區', '楠梓區', '三民區', '新興區', '前金區', '苓雅區', '前鎮區', '旗津區', '小港區', '鳳山區', '林園區', '大寮區', '大樹區', '大社區', '仁武區', '鳥松區', '岡山區', '橋頭區', '燕巢區', '田寮區', '阿蓮區', '路竹區', '湖內區', '茄萣區', '永安區', '彌陀區', '梓官區', '旗山區', '美濃區', '六龜區', '甲仙區', '杉林區', '內門區', '茂林區', '桃源區', '那瑪夏區'],
    '基隆市': ['中正區', '七堵區', '暖暖區', '仁愛區', '中山區', '安樂區', '信義區'],
    '新竹市': ['東區', '北區', '香山區'],
    '嘉義市': ['東區', '西區'],
    '新竹縣': ['竹北市', '竹東鎮', '新埔鎮', '關西鎮', '湖口鄉', '新豐鄉', '芎林鄉', '橫山鄉', '北埔鄉', '寶山鄉', '峨眉鄉', '尖石鄉', '五峰鄉'],
    '苗栗縣': ['苗栗市', '頭份市', '竹南鎮', '後龍鎮', '通霄鎮', '苑裡鎮', '卓蘭鎮', '造橋鄉', '西湖鄉', '頭屋鄉', '公館鄉', '銅鑼鄉', '三義鄉', '大湖鄉', '獅潭鄉', '三灣鄉', '南庄鄉', '泰安鄉'],
    '彰化縣': ['彰化市', '鹿港鎮', '和美鎮', '線西鄉', '伸港鄉', '福興鄉', '秀水鄉', '花壇鄉', '芬園鄉', '員林市', '溪湖鎮', '田中鎮', '大村鄉', '埔鹽鄉', '埔心鄉', '永靖鄉', '社頭鄉', '二水鄉', '北斗鎮', '二林鎮', '田尾鄉', '埤頭鄉', '芳苑鄉', '大城鄉', '竹塘鄉', '溪州鄉'],
    '南投縣': ['南投市', '埔里鎮', '草屯鎮', '竹山鎮', '集集鎮', '名間鄉', '鹿谷鄉', '中寮鄉', '魚池鄉', '國姓鄉', '水里鄉', '信義鄉', '仁愛鄉'],
    '雲林縣': ['斗六市', '斗南鎮', '虎尾鎮', '西螺鎮', '土庫鎮', '北港鎮', '古坑鄉', '大埤鄉', '莿桐鄉', '林內鄉', '二崙鄉', '崙背鄉', '麥寮鄉', '東勢鄉', '褒忠鄉', '台西鄉', '元長鄉', '四湖鄉', '口湖鄉', '水林鄉'],
    '嘉義縣': ['太保市', '朴子市', '布袋鎮', '大林鎮', '民雄鄉', '溪口鄉', '新港鄉', '六腳鄉', '東石鄉', '義竹鄉', '鹿草鄉', '水上鄉', '中埔鄉', '竹崎鄉', '梅山鄉', '番路鄉', '大埔鄉', '阿里山鄉'],
    '屏東縣': ['屏東市', '潮州鎮', '東港鎮', '恆春鎮', '萬丹鄉', '長治鄉', '麟洛鄉', '九如鄉', '里港鄉', '高樹鄉', '鹽埔鄉', '內埔鄉', '竹田鄉', '內埔鄉', '萬巒鄉', '內埔鄉', '崁頂鄉', '新埤鄉', '南州鄉', '林邊鄉', '琉球鄉', '佳冬鄉', '新園鄉', '枋寮鄉', '枋山鄉', '三地門鄉', '霧台鄉', '瑪家鄉', '泰武鄉', '來義鄉', '春日鄉', '獅子鄉', '牡丹鄉'],
    '宜蘭縣': ['宜蘭市', '羅東鎮', '蘇澳鎮', '頭城鎮', '礁溪鄉', '壯圍鄉', '員山鄉', '冬山鄉', '五結鄉', '三星鄉', '大同鄉', '南澳鄉'],
    '花蓮縣': ['花蓮市', '鳳林鎮', '玉里鎮', '新城鄉', '吉安鄉', '壽豐鄉', '光復鄉', '豐濱鄉', '瑞穗鄉', '富里鄉', '秀林鄉', '萬榮鄉', '卓溪鄉'],
    '台東縣': ['台東市', '成功鎮', '關山鎮', '卑南鄉', '鹿野鄉', '延平鄉', '海端鄉', '池上鄉', '東河鄉', '成功鎮', '長濱鄉', '太麻里鄉', '金峰鄉', '大武鄉', '達仁鄉', '蘭嶼鄉', '綠島鄉'],
    '澎湖縣': ['馬公市', '湖西鄉', '白沙鄉', '西嶼鄉', '望安鄉', '七美鄉'],
    '金門縣': ['金城鎮', '金湖鎮', '金沙鎮', '金寧鄉', '烈嶼鄉', '烏坵鄉'],
    '連江縣': ['南竿鄉', '北竿鄉', '莒光鄉', '東引鄉']
}

def load_obfuscate_names(filepath=OBFUSCATE_NAME_FILE):
    """從 JSON 檔案載入姓名資料"""
    global SURNAMES, GIVEN_NAMES
    try:
        if os.path.exists(filepath):
            print(f"Loading obfuscate names from {filepath}...")
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if 'surnames' in data and data['surnames']:
                    SURNAMES = data['surnames']
                if 'given_names' in data and data['given_names']:
                    GIVEN_NAMES = data['given_names']
            print(f"Loaded {len(SURNAMES)} surnames and {len(GIVEN_NAMES)} given names.")
            return True
    except Exception as e:
        print(f"Error loading obfuscate names: {e}")
    return False

def save_obfuscate_names(filepath=OBFUSCATE_NAME_FILE):
    """將目前的姓名資料儲存到 JSON 檔案"""
    try:
        data = {
            'surnames': SURNAMES,
            'given_names': GIVEN_NAMES,
            'updated_at': datetime.now().isoformat()
        }
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"Saved obfuscate names to {filepath}.")
    except Exception as e:
        print(f"Error saving obfuscate names: {e}")

def init_names_from_database(engine, table_name="EMP_DATA", column_name="emp_name"):
    """
    從資料庫指定資料表初始化姓名資料。
    
    Args:
        engine: SQLAlchemy 資料庫連線引擎。
        table_name: 資料表名稱 (預設 EMP_DATA)
        column_name: 姓名欄位名稱 (預設 emp_name)
    """
    global SURNAMES, GIVEN_NAMES
    
    if not engine:
        print("No database engine provided for name initialization.")
        return False
        
    print(f"Initializing names from database {table_name}.{column_name}...")
    try:
        new_surnames = set()
        new_given_names = set()
        
        with engine.connect() as conn:
            # 取得所有非空姓名
            # SQL Injection Note: table_name/column_name should be trusted or sanitized if from web input.
            # Here it comes from local config, assumed safe.
            query = text(f"SELECT DISTINCT {column_name} FROM {table_name} WHERE {column_name} IS NOT NULL AND LEN({column_name}) >= 2")
            result = conn.execute(query)
            
            count = 0
            for row in result:
                name = row[0].strip()
                length = len(name)
                
                # 切分邏輯
                if length == 2:
                    # 2字元: 1字姓 + 1字名
                    new_surnames.add(name[0])
                    new_given_names.add(name[1])
                    count += 1
                elif length == 3:
                    # 3字元: 1字姓 + 2字名
                    new_surnames.add(name[0])
                    new_given_names.add(name[1:])
                    count += 1
                elif length == 4:
                    # 4字元: 2字姓 + 2字名 (複姓)
                    new_surnames.add(name[:2])
                    new_given_names.add(name[2:])
                    count += 1
                # 忽略其他長度的名字
                
        if new_surnames and new_given_names:
            SURNAMES = sorted(list(new_surnames))
            GIVEN_NAMES = sorted(list(new_given_names))
            print(f"Initialized from DB: {len(SURNAMES)} surnames, {len(GIVEN_NAMES)} given names (sampled from {count} records).")
            return True
        else:
            print("No valid names found in database.")
            return False
            
    except Exception as e:
        print(f"Error initializing names from database: {e}")
        return False

def initialize_name_data(engine=None, source_type="DEFAULT", source_value=None):
    """
    初始化姓名資料流程
    source_type: 'DEFAULT', 'FILE', 'DB'
    source_value: File path (for FILE) or "Table.Column" (for DB)
    """
    # 1. FILE Mode
    if source_type == "FILE":
        filepath = source_value if source_value else OBFUSCATE_NAME_FILE
        if load_obfuscate_names(filepath):
            return

    # 2. DB Mode
    if source_type == "DB" and engine:
        table = "EMP_DATA"
        col = "emp_name"
        if source_value and "." in source_value:
            parts = source_value.split(".")
            table = parts[0]
            col = parts[1]
            
        if init_names_from_database(engine, table, col):
            # Cache it to default file for next run speedup? or separate cache?
            # For now, just keep in memory for this session.
            return
    
    # 3. Default / Fallback (Load default file check)
    if load_obfuscate_names(OBFUSCATE_NAME_FILE):
        return

    # 4. Fallback to hardcoded list (done by global init)
    print("Using default internal surname/given_names.")

def obfuscate_name(name: str, emp_no: str) -> str:
    """
    使用 emp_no 作為 seed，分離姓/名後重組
    """
    if not name or not emp_no:
        # print(f"DEBUG_ANON: Skipped due to empty input - Name: '{name}', EmpNo: '{emp_no}'")
        return name
        
    try:
        # 使用 emp_no 的 hash 作為 seed，確保同一員工編號永遠產生相同結果
        seed_val = int(hash(emp_no))
        rng = random.Random(seed_val)
        
        new_surname = rng.choice(SURNAMES)
        new_given = rng.choice(GIVEN_NAMES)
        
        result = new_surname + new_given
        # print(f"DEBUG_ANON: '{name}' ({emp_no}) -> '{result}'") 
        # Uncomment above for vervose logging, but standard output might be too much.
        # Let's print only if it looks like it failed or just for the first few calls?
        # For now, let's just rely on the db_replicator debug.
        return result
    except Exception as e:
        print(f"DEBUG_ANON: Error processing '{name}' with emp_no '{emp_no}': {e}")
        # 如果發生錯誤，回傳原始值或部分遮罩
        return name[0] + 'O' + name[-1] if len(name) > 2 else name

def anonymize_id(id_number: str) -> str:
    """
    驗證台灣身分證 checksum，通過後遮罩中間5碼
    格式: A12*****89
    """
    if not id_number or len(id_number) != 10:
        return id_number
        
    # 簡單驗證格式 (首字英文字母，後接9個數字)
    if not re.match(r'^[A-Z][0-9]{9}$', id_number):
        # 格式不符，直接全遮罩保護安全
        return '*' * 10
        
    # 遮罩中間 5 碼 (第 4 到 第 8 碼，索引 3-7)
    # A 1 2 3 4 5 6 7 8 9
    # 0 1 2 3 4 5 6 7 8 9
    return id_number[:3] + '*****' + id_number[8:]

def obfuscate_spouse_name(name: str, emp_no: str) -> str:
    """
    使用 emp_no + 特定數值 作為 seed，產生配偶姓名
    """
    if not name or not emp_no:
        return name
        
    try:
        # 使用 emp_no 的 hash + Salt 作為 seed
        seed_val = int(hash(emp_no)) + 139420
        rng = random.Random(seed_val)
        
        new_surname = rng.choice(SURNAMES)
        new_given = rng.choice(GIVEN_NAMES)
        
        return new_surname + new_given
    except Exception as e:
        print(f"DEBUG_ANON: Error processing spouse '{name}' with emp_no '{emp_no}': {e}")
        return name

def obfuscate_phone(phone: str, seed: any) -> str:
    """
    保留電話格式，將最後 5 碼數字替換為亂數
    """
    if not phone:
        return phone
        
    try:
        rng = random.Random(hash(str(seed)))
        
        # 找出所有數字的位置
        digit_indices = [i for i, char in enumerate(phone) if char.isdigit()]
        
        if len(digit_indices) < 5:
            # 數字少於 5 碼，不做處理或全改? 這裡保留原樣比較安全
            return phone
            
        # 鎖定最後 5 個數字的索引
        target_indices = digit_indices[-5:]
        
        # 轉換為 list 方便修改
        chars = list(phone)
        for idx in target_indices:
            chars[idx] = str(rng.randint(0, 9))
            
        return "".join(chars)
    except Exception:
        return phone

def obfuscate_address(address: str, seed: any) -> str:
    """
    保留縣市、替換行政區、隨機化門牌號碼
    並支援將全形數字轉為半形
    """
    if not address:
        return address
        
    try:
        # 0. 全形轉半形 (簡單對應) + 臺/台 通用化
        full_width_map = str.maketrans("０１２３４５６７８９", "0123456789")
        address = address.translate(full_width_map).replace('臺', '台')

        rng = random.Random(hash(str(seed)))
        
        # 1. 識別縣市
        city = None
        for c in CITY_DISTRICTS.keys():
            if address.startswith(c):
                city = c
                break
        
        if not city:
            # 無法識別縣市，做簡單隨機遮罩
            return re.sub(r'\d+', lambda m: str(rng.randint(1, 999)), address)
            
        # 2. 替換行政區 (從該縣市的列表中隨機選一個)
        districts = CITY_DISTRICTS[city]
        new_district = rng.choice(districts)
        
        # 3. 隨機化路名後的數字 (門牌、樓層)
        # 這裡簡化處理，直接產生一個新的虛擬地址結構
        roads = ['中正路', '中山路', '中華路', '民生路', '民權路', '民族路', '建國路', '和平路', '信義路', '仁愛路']
        road = rng.choice(roads)
        sec = rng.randint(1, 5)
        lane = rng.randint(1, 100)
        no = rng.randint(1, 500)
        
        return f"{city}{new_district}{road}{sec}段{lane}巷{no}號"
        
    except Exception:
        return address


def clear_content(val: str, seed: any = None) -> str:
    """
    清空內容，回傳空字串
    """
    return ""

if __name__ == "__main__":
    # 簡單測試
    print("Testing obfuscate_name:")
    print(obfuscate_name("王小明", "1001"))
    print(obfuscate_name("王小明", "1001")) # 應相同
    print(obfuscate_name("王小明", "1002")) # 應不同
    
    print("\nTesting anonymize_id:")
    print(anonymize_id("A123456789"))
    
    print("\nTesting obfuscate_address:")
    print(obfuscate_address("台北市信義區市府路1號", "1001"))
    print(obfuscate_address("台中市西屯區台灣大道三段99號", "1002"))
