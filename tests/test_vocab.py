import json

from scribe import vocab


def test_vocab_seed_written_when_missing(tmp_path):
    terms = vocab.load_terms(tmp_path)
    on_disk = json.loads((tmp_path / "vocab.json").read_text(encoding="utf-8"))
    assert on_disk == terms
    pan = [t for t in terms if t["term"] == "新桥"]
    assert pan and "心桥" in pan[0]["aliases"]
    assert len(terms) == 10


def test_vocab_existing_file_is_read(tmp_path):
    (tmp_path / "vocab.json").write_text(json.dumps([{"term": "X", "aliases": [], "note": "n"}]),
                                         encoding="utf-8")
    assert vocab.load_terms(tmp_path) == [{"term": "X", "aliases": [], "note": "n"}]


def test_terms_line_format():
    terms = [{"term": "BABEL", "aliases": ["备播"], "note": "项目名"},
             {"term": "GLM", "aliases": [], "note": "大模型"}]
    assert vocab.terms_line(terms) == "BABEL(项目名;常被识别成 备播)、GLM(大模型)"


def test_alias_correction_prefers_longer_aliases_and_preserves_word_boundaries():
    correct = vocab.build_alias_corrector(vocab.SEED)
    assert correct("备播的 Cloud Code、Cloud、Deep Seek。") == "BABEL的 Claude Code、Claude、DeepSeek。"
    assert correct("Bebop、Cloudy、cloud") == "Bebop、Cloudy、cloud"


def test_alias_correction_does_not_replace_its_own_output():
    correct = vocab.build_alias_corrector([
        {"term": "甲乙", "aliases": ["甲一"]},
        {"term": "甲丙", "aliases": ["甲乙"]},
    ])
    assert correct("甲一，甲乙") == "甲乙，甲丙"


def test_pinyin_correction_is_opt_in_per_term():
    entry = {"term": "新桥", "aliases": ["新乔"]}
    original = "把结果发给欣桥，再请新乔看一下。"
    assert vocab.build_corrector([entry])(original) == "把结果发给欣桥，再请新桥看一下。"
    assert vocab.build_corrector([dict(entry, pinyin=True)])(original) == "把结果发给新桥，再请新桥看一下。"


def test_pinyin_correction_preserves_known_words_and_ambiguous_matches():
    correct = vocab.build_corrector([
        {"term": "新桥", "pinyin": True},
        {"term": "辛侨"},
        {"term": "回撤", "pinyin": True},
    ])
    assert correct("按一下回车，再发给欣桥。") == "按一下回车，再发给欣桥。"

    correct = vocab.build_corrector([{"term": "新桥", "pinyin": True}, {"term": "欣侨文"}])
    assert correct("欣侨文，欣桥。") == "欣侨文，新桥。"


def test_pinyin_correction_does_not_overwrite_exact_alias_or_cross_punctuation():
    correct = vocab.build_corrector([
        {"term": "新桥", "pinyin": True},
        {"term": "欣侨", "aliases": ["裁判组"]},
    ])
    assert correct("裁判组、欣，桥、欣僻。") == "欣侨、欣，桥、欣僻。"
    correct = vocab.build_corrector([{"term": "新桥", "pinyin": True}, {"term": "编辑", "aliases": ["欣桥"]}])
    assert correct("欣桥。") == "编辑。"


def test_hotwords_keep_whole_terms_and_rank_by_hits_with_stable_ties():
    terms = [
        {"term": "DEMO TOOLS"},
        {"term": "最常用", "hits": 15},
        {"term": "不适合的长词" * 20, "hits": 30},
        {"term": "次常用", "hits": 15},
        {"term": "DEMO TOOLS", "hits": 1},
        {"term": "最后", "hits": None},
    ]
    assert vocab.hotword_terms(terms) == ["最常用", "次常用", "DEMO TOOLS", "最后"]
    assert vocab.hotwords(terms) == "最常用 次常用 DEMO TOOLS 最后"

    many = [{"term": f"词{i}"} for i in range(20)]
    assert vocab.hotword_terms(many) == [f"词{i}" for i in range(15)]
    long = [{"term": str(i) * 31} for i in range(9)]
    assert len(vocab.hotwords(long)) == 95


def test_relevant_terms_finds_aliases_exact_terms_and_possible_homophones():
    pan = {"term": "新桥", "aliases": []}
    babel = {"term": "BABEL", "aliases": ["备播"]}
    glm = {"term": "GLM"}
    other = {"term": "Claude", "aliases": ["Cloud"]}
    terms = [pan, babel, glm, other]
    assert vocab.relevant_terms(terms, "请欣桥看一下备播", "GLM4、Cloudy") == [babel, pan]
    assert vocab.relevant_terms(terms, "改好的 babel。", "用glm整理。") == [babel, glm]
    assert vocab.relevant_terms([pan], "盘", "席") == []

    many = [{"term": f"词{i}"} for i in range(30)]
    assert vocab.relevant_terms(many, "、".join(entry["term"] for entry in many), "") == many[:20]
