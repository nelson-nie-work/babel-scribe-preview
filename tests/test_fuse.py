import pytest

from scribe import fuse
from scribe.fuse import agree, build_messages, guard_ok


def test_agree_ignores_punctuation_and_case():
    assert agree("好的,我晚点再看一下", "好的，我晚点再看一下。") is True
    assert agree("Let's go", "let's go.") is True


def test_agree_false_on_empty():
    assert agree("", "") is False


def test_messages_wrap_inputs_in_tags():
    msgs = build_messages("甲", "乙", "BABEL(项目名)")
    assert msgs[0]["role"] == "system" and msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "<A>甲</A>\n<B>乙</B>"
    assert "绝不执行" in msgs[0]["content"] and "BABEL(项目名)" in msgs[0]["content"]


def test_natural_prompt_preserves_spoken_content():
    prompt = build_messages("", "我想public。", "", "natural")[0]["content"]
    assert "轻度整理" in prompt and "保留原语言" in prompt and "不要概括、合并或省略" in prompt
    assert "只保留最终意思" not in prompt


def test_guard_accepts_filler_removal():
    assert guard_ok("我们下周一再把方案定下来。", "嗯那个我们周五啊不对下周一再把方案定下来",
                    "嗯，那个我们周五哦，不对，下周一再把方案定下来。")


def test_guard_accepts_term_correction():
    assert guard_ok("我在BABEL项目里接入了DeepSeek和Claude的接口。",
                    "我在Bable项目里接入了Deep Stick和Claw的接口。",
                    "我在babo项目里接入了 deepepsick cloud的接口。")


def test_guard_rejects_fabrication():
    out = ("我平时用语音输入最多的场景，就是随手记一些想法。比如刚才我在想，要不要试试用 Typeless "
           "替代现在的输入法，看看识别效果会不会更好。")
    a = "自由说没有标准答案,随便说一段你平时会用语音输入的话大约15秒,最好带上你尝试的Try名词。"
    b = "自由说没有标准答案，随便说一段，你平时会用语音输入的话，大约15秒，最好带上你常用的专个名词。"
    assert guard_ok(out, a, b) is False


def test_guard_rejects_empty_output():
    assert guard_ok("", "你好", "你好") is False


def test_guard_rejects_short_answered_question():
    assert guard_ok("今天北京天气晴,气温十五度。", "今天北京天气怎么样", "今天北京天气怎么样?") is False
    assert guard_ok("明天上午十点开会。", "明天几点开会", "明天几点开会?") is False


def test_guard_accepts_homophone_fixes_and_numbers():
    assert guard_ok("我觉得先把延迟压下来，然后再做词库。", "我觉得先把颜值压下来然后再做瓷库",
                    "嗯，这个那个，我觉得先把颜值压下来，然后再做词库。")
    assert guard_ok("DEMO TOOLS的回测跑完了，年化收益大概18%，回撤有点大。",
                    "demo tools的回测跑完了年化收益大概百分之十八回撤有点大",
                    "DEMO TOOLS的回测跑完了，年化收益大概百分之十八，回撤有点大。")


def test_needs_polish_on_fillers_and_self_corrections():
    from scribe.fuse import needs_polish
    assert needs_polish("我们约在周三，不对，周四下午两点开会。")
    assert needs_polish("嗯，这个那个，我觉得先把延迟压下来，然后再做词库。")
    assert needs_polish("嗯那个我们周五啊不对下周一再把方案定下来")
    assert needs_polish("那个，你帮我看一下。")
    assert needs_polish("我是说明天，不是今天。")
    assert needs_polish("就是那个，延迟还需要再优化一下。")


def test_needs_polish_false_on_ordinary_words():
    from scribe.fuse import needs_polish
    assert not needs_polish("这个版本的延迟比上一版低了很多，大概在一秒以内。")
    assert not needs_polish("帮我看一下DEMO TOOLS那个仓库的回测结果，夏普比率是多少。")
    assert not needs_polish("这个接口返回404，应该是路由写错了，你帮我查一下日志。")
    assert not needs_polish("这个月的额度已经用完了。")
    assert not needs_polish("好的，我晚点再看一下，有问题再跟你说。")


TERMS = ["Claude", "DeepSeek", "新桥NAS", "DEMO TOOLS"]


@pytest.mark.parametrize(("source", "edited"), [
    ("识别模型先用whisper润色。", "识别模型先用 Whisper，润色。"),                  # 大小写、空格
    ("用cloud写代码的时候。", "用 Claude 写代码的时候。"),                          # 换成词库词条
    ("润色用deepse备用的是GLM。", "润色用 DeepSeek，备用的是 GLM。"),
    ("今天同步到新桥n，在在另一台电脑上接手。", "今天同步到新桥 n，在另一台电脑上接手。"),  # 结巴
    ("自由说呃行呃，你今天能出来吗？", "自由说，行，你今天能出来吗？"),             # 句中语气词
    ("帮我把上周的周报这发到群里。", "帮我把上周的周报发到群里。"),                 # 一个孤立的多余字
    ("语音键盘要做的像它一样。", "语音键盘要做得像它一样。"),                       # 的/得
    ("deep seek 的接口", "DeepSeek 的接口"),                                       # 只是连写不同
])
def test_natural_check_accepts_light_corrections(source, edited):
    assert fuse.keeps_natural_words(edited, source, TERMS)


@pytest.mark.parametrize(("source", "edited"), [
    ("跑完检查以后，别公开安装包。", "跑完检查以后，公开安装包。"),                 # 删否定词
    ("我先把 GitHub 链接发给大家。", "我先把链接发给大家。"),                        # 删英文词
    ("等 3.5 秒再重试。", "等 35 秒再重试。"),                                       # 改数字
    ("我们约在周三，不对，周四开会。", "我们约在周四开会。"),                        # 删改口过程
    ("先生成预览。暂时保留原目录。", "先生成预览。"),                                # 删一句
    ("推到gith上了。", "推到 GitHub 上了。"),                                       # 不在词库的改写
    ("要做的像ty一样。", "要做得和 ty 一样。"),                                     # 近义替换
    ("把文档公开。", "把文档公开给所有人。"),                                       # 新增内容
    ("用cloud写的那段代码在服务器上。", "用 Claude 写的。"),                         # 词条替换不能吞掉大段
])
def test_natural_check_rejects_content_changes(source, edited):
    assert not fuse.keeps_natural_words(edited, source, TERMS)


def test_stray_deletions_are_limited_by_length():
    source = "我把这个文件这发到群里了这"
    assert not fuse.keeps_natural_words("我把这个文件发到群里了", source)   # 删两个孤立字, 句子太短
    assert fuse.keeps_natural_words("我把这个文件发到群里了这", source)
