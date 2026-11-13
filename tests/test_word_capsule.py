from scribe.word_capsule import WordCapsule


def test_suggestion_grows_immediately_after_a_short_notice(qapp_offscreen):
    pill = WordCapsule()
    pill.notice("已撤销收词")
    width = pill.width()
    pill.dismiss()
    pill.suggest("青砚桥", lambda: None)
    try:
        assert pill.width() > width
        assert pill._timer.interval() == 11000
        assert pill._message.width() >= pill._message.sizeHint().width()
        assert pill._action.width() >= pill._action.sizeHint().width()
        assert pill._skip.width() >= pill._skip.sizeHint().width()
    finally:
        pill.dismiss()
