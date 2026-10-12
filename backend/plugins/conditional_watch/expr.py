"""26 条件盯盘：安全条件表达式求值器（不使用 eval）。

支持：price/change_percent/turnover_rate/volume/volume_ratio 及自定义变量；
运算符 > >= < <= == != and or（and 优先于 or），括号。
"""
import re

_TOKEN_RE = re.compile(
    r"\s*(>=|<=|==|!=|>|<|\(|\)|\band\b|\bor\b|[A-Za-z_][A-Za-z0-9_]*|-?\d+(?:\.\d+)?)"
)

_COMPARE_OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
                "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
                "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


def tokenize(expr: str) -> list:
    tokens = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN_RE.match(expr, pos)
        if not m:
            rest = expr[pos:].strip()
            if rest:
                raise ValueError(f"无法解析表达式片段: {rest[:16]}")
            break
        tokens.append(m.group(1))
        pos = m.end()
    return tokens


class _Parser:
    def __init__(self, tokens, values):
        self.tokens = tokens
        self.pos = 0
        self.values = values

    def _peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _advance(self):
        tok = self._peek()
        self.pos += 1
        return tok

    def parse_or(self):
        left = self.parse_and()
        while self._peek() == "or":
            self._advance()
            right = self.parse_and()
            left = left or right
        return left

    def parse_and(self):
        left = self.parse_cmp()
        while self._peek() == "and":
            self._advance()
            right = self.parse_cmp()
            left = left and right
        return left

    def parse_cmp(self):
        left = self.parse_atom()
        op = self._peek()
        if op in _COMPARE_OPS:
            self._advance()
            right = self.parse_atom()
            if left is None or right is None:
                return False
            try:
                return _COMPARE_OPS[op](left, right)
            except TypeError:
                return False
        # 单值布尔（如变量本身为真）
        return bool(left)

    def parse_atom(self):
        tok = self._peek()
        if tok == "(":
            self._advance()
            val = self.parse_or()
            if self._peek() == ")":
                self._advance()
            return val
        if tok is None:
            return None
        self._advance()
        if tok in self.values:
            return self.values[tok]
        try:
            return float(tok)
        except ValueError:
            return None


def evaluate(condition: str, values: dict) -> bool:
    """对条件表达式求值。变量缺失时比较恒为 False（保守不触发）。"""
    if not condition or not condition.strip():
        return False
    try:
        tokens = tokenize(condition)
        if not tokens:
            return False
        return bool(_Parser(tokens, values).parse_or())
    except (ValueError, IndexError):
        return False


def build_values(market_data: dict, variables: dict = None) -> dict:
    """将行情字段 + 自定义变量合并为求值变量表。"""
    values = dict(variables or {})
    values.update({
        "price": _num(market_data.get("最新价") or market_data.get("price")),
        "change_percent": _num(market_data.get("涨跌幅") or market_data.get("change_percent")),
        "turnover_rate": _num(market_data.get("换手率") or market_data.get("turnover_rate")),
        "volume": _num(market_data.get("成交量") or market_data.get("volume")),
        "volume_ratio": _num(market_data.get("量比") or market_data.get("volume_ratio")),
    })
    return values


def _num(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n else None
