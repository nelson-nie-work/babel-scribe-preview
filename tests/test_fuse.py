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
                    "AI Stop的回测跑完了年化收益大概百分之十八回撤有点大",
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
