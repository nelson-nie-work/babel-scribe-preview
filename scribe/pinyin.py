"""旧 BABEL 听写的拼音表，加上 Scribe 词库示例用字。

只覆盖表内发音。未知字、标点和空格不参与同音匹配。
"""

PINYIN = {
    "新": "xin", "欣": "xin", "辛": "xin", "心": "xin",
    "桥": "qiao", "乔": "qiao", "侨": "qiao",
    "断": "duan", "段": "duan", "句": "ju", "落": "luo",
    "帮": "bang", "我": "wo", "把": "ba", "这": "zhe", "个": "ge",
    "参": "can", "数": "shu", "调": "tiao", "成": "cheng", "自": "zi",
    "动": "dong", "模": "mo", "式": "shi", "然": "ran", "后": "hou",
    "保": "bao", "存": "cun", "一": "yi", "下": "xia", "配": "pei",
    "置": "zhi", "文": "wen", "件": "jian", "完": "wan", "全": "quan",
    "不": "bu", "同": "tong", "结": "jie", "构": "gou", "的": "de",
    "话": "hua", "熵": "shang", "增": "zeng", "曾": "ceng",
    "判": "pan", "盘": "pan", "潘": "pan", "盼": "pan", "席": "xi",
    "洗": "xi", "西": "xi", "稀": "xi", "系": "xi", "汉": "han",
    "词": "ci", "瓷": "ci", "磁": "ci", "辞": "ci", "库": "ku", "裤": "ku",
    "延": "yan", "颜": "yan", "言": "yan", "严": "yan", "迟": "chi",
    "持": "chi", "池": "chi", "值": "zhi", "回": "hui", "撤": "che", "车": "che",
    "飞": "fei", "非": "fei", "牛": "niu", "纽": "niu", "钮": "niu",
    "智": "zhi", "志": "zhi", "治": "zhi", "谱": "pu", "普": "pu",
    "备": "bei", "北": "bei", "播": "bo", "波": "bo", "银": "yin", "纸": "zhi",
}


def syllables(text: str) -> tuple[str, ...] | None:
    """逐字返回无声调音节，未知写法返回 None。"""
    if not text or any(char not in PINYIN for char in text):
        return None
    return tuple(PINYIN[char] for char in text)
