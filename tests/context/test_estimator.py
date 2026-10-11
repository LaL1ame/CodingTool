"""Token 估算模块单测(N1)。"""

from codepilot.context.estimator import TokenEstimator, estimate_text


class TestEstimateText:
    def test_code_chars_over_4(self):
        assert estimate_text("a" * 1000, kind="code") == 250

    def test_prose_words_times_1_3(self):
        # 20 个词 → 26 token (floor of 20*1.3)
        text = " ".join(["word"] * 20)
        assert estimate_text(text, kind="prose") == 26

    def test_empty_text(self):
        assert estimate_text("") == 0

    def test_code_at_least_one(self):
        assert estimate_text("ab", kind="code") == 1  # 2//4=0 → 保底 1


class TestTokenEstimator:
    def test_init_zero(self):
        est = TokenEstimator()
        assert est.current() == 0

    def test_init_anchor(self):
        est = TokenEstimator(last_input_tokens=1000)
        assert est.current() == 1000

    def test_add_increments_and_reports(self):
        est = TokenEstimator(last_input_tokens=0)
        inc = est.add("a" * 400)  # 100 token
        assert inc == 100
        assert est.current() == 100

    def test_anchor_resets_delta(self):
        est = TokenEstimator(last_input_tokens=0)
        est.add("a" * 400)  # delta=100
        est.anchor(5000)  # 重锚,增量清零
        assert est.current() == 5000
        est.add("a" * 800)  # 新增量 200
        assert est.current() == 5200

    def test_anchor_absorbs_error(self):
        # 估算有误差,重锚后以权威值为准
        est = TokenEstimator(last_input_tokens=900)
        est.add("b" * 400)
        est.anchor(1234)
        assert est.current() == 1234