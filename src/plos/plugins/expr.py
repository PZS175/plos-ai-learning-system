"""插件共享：安全的数学表达式求值器。

供「科学计算器」「函数图像绘制」等插件复用，避免各处重复实现。

安全设计：**不使用 eval**。表达式先用 ``ast`` 解析成语法树，
再按白名单逐节点求值——只允许数字常量、已注册变量、π/e/Ans 常量、
白名单函数，以及 + - * / // % ** 与一元正负号；
``__import__``、属性访问、下标、lambda 等一律拒绝。
"""

from __future__ import annotations

import ast
import math
import operator
import re
from typing import Callable, Dict, Iterable, Optional

_BINARY: Dict[type, Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY: Dict[type, Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# 三角 / 反三角函数受角度制影响，单独处理
_TRI_FORWARD = {"sin": math.sin, "cos": math.cos, "tan": math.tan}
_TRI_INVERSE = {"asin": math.asin, "acos": math.acos, "atan": math.atan}

_PLAIN_FUNCTIONS: Dict[str, Callable[[float], float]] = {
    "sqrt": math.sqrt,
    "log": math.log10,
    "lg": math.log10,
    "ln": math.log,
    "exp": math.exp,
    "abs": abs,
    "round": round,
    "floor": math.floor,
    "ceil": math.ceil,
    "sign": lambda v: (v > 0) - (v < 0),
}

#: 支持的函数名（供界面提示与校验）
FUNCTION_NAMES = tuple(
    sorted(list(_TRI_FORWARD) + list(_TRI_INVERSE) + list(_PLAIN_FUNCTIONS))
)

#: 支持的常量名
CONSTANT_NAMES = ("pi", "π", "e", "ans")

#: 已知标识符（函数名 + 常量名），用于隐式乘法判定
_KNOWN_NAMES = {name.lower() for name in FUNCTION_NAMES} | {"pi", "e", "ans"}

_IDENT_CALL_RE = re.compile(r"([A-Za-zπ_][A-Za-z0-9_]*)\s*\(")
_NUM_THEN_SYMBOL_RE = re.compile(r"(?<=[0-9])(?=[A-Za-zπ(])(?![eE][0-9])")
_CLOSE_THEN_SYMBOL_RE = re.compile(r"(?<=[)πx])(?=[0-9A-Za-zπ(])")


def add_implicit_multiplication(text: str) -> str:
    """把中学常见省略写法补上乘号：``2x`` → ``2*x``、``3(x+1)`` → ``3*(x+1)``。

    已知函数名后的括号（如 ``sin(``）不会被改写。
    """

    def _replace_call(match: re.Match) -> str:
        name = match.group(1)
        if name.lower() in _KNOWN_NAMES:
            return match.group(0)
        return f"{name}*("

    text = _IDENT_CALL_RE.sub(_replace_call, text)
    text = _NUM_THEN_SYMBOL_RE.sub("*", text)
    text = _CLOSE_THEN_SYMBOL_RE.sub("*", text)
    return text


def normalize_expression(expression: str) -> str:
    """统一全角 / 习惯写法：^ × ÷ − 与 π 转为 Python 可解析形式。"""
    text = str(expression or "").strip()
    for src, dst in (("^", "**"), ("×", "*"), ("÷", "/"), ("−", "-"), ("　", " ")):
        text = text.replace(src, dst)
    return add_implicit_multiplication(text)


class ExprEvaluator:
    """白名单表达式求值器。

    Args:
        variables: 允许出现的变量名（默认 ``{"x"}``，供函数绘图使用）。
        degrees: 三角函数是否按角度制（计算器默认 True，绘图默认 False）。
        ans: ``Ans`` 常量取值。
    """

    def __init__(
        self,
        variables: Optional[Iterable[str]] = None,
        degrees: bool = True,
        ans: float = 0.0,
    ) -> None:
        self.variables = set(variables) if variables is not None else {"x"}
        self.degrees = degrees
        self.ans = ans

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def compile(self, expression: str) -> ast.AST:
        """把表达式解析为语法树，供批量求值复用（如绘图采样）。"""
        text = normalize_expression(expression)
        if not text:
            raise ValueError("表达式为空")
        tree = ast.parse(text, mode="eval")
        return tree.body

    def eval(self, expression: str, **values: float) -> float:
        """解析并求值一次。"""
        return self.eval_compiled(self.compile(expression), **values)

    def eval_compiled(self, node: ast.AST, **values: float) -> float:
        """对已解析的语法树求值，可传入变量取值。"""
        return float(self._eval_node(node, values))

    # ------------------------------------------------------------------
    # 内部求值
    # ------------------------------------------------------------------
    def _constant(self, name: str) -> float:
        key = name.lower()
        if key in ("pi", "π"):
            return math.pi
        if key == "e":
            return math.e
        if key == "ans":
            return float(self.ans)
        raise ValueError(f"不支持的常量：{name}")

    def _call(self, name: str, arg: float) -> float:
        key = name.lower()
        if key in _TRI_FORWARD:
            return _TRI_FORWARD[key](math.radians(arg) if self.degrees else arg)
        if key in _TRI_INVERSE:
            value = _TRI_INVERSE[key](arg)
            return math.degrees(value) if self.degrees else value
        if key in _PLAIN_FUNCTIONS:
            return _PLAIN_FUNCTIONS[key](arg)
        raise ValueError(f"不支持的函数：{name}")

    def _eval_node(self, node: ast.AST, values: Dict[str, float]) -> float:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError("只允许数字常量")
            return float(node.value)

        if isinstance(node, ast.Name):
            if node.id in values:
                return float(values[node.id])
            return self._constant(node.id)

        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            return _BINARY[type(node.op)](
                self._eval_node(node.left, values),
                self._eval_node(node.right, values),
            )

        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](self._eval_node(node.operand, values))

        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError("非法调用")
            if len(node.args) != 1 or node.keywords:
                raise ValueError("函数只接受一个参数")
            return self._call(node.func.id, self._eval_node(node.args[0], values))

        raise ValueError("表达式含不允许的写法")


def referenced_names(node: ast.AST) -> set:
    """列出表达式中引用到的变量名，用于判断依赖是否齐备。

    函数名（sqrt、log 等）与内置常量（π、e、Ans）都不算依赖，会被排除。
    """
    function_nodes = {
        id(child.func)
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }
    builtin = {name.lower() for name in CONSTANT_NAMES}
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name)
        and id(child) not in function_nodes
        and child.id.lower() not in builtin
    }


__all__ = [
    "ExprEvaluator",
    "FUNCTION_NAMES",
    "CONSTANT_NAMES",
    "normalize_expression",
    "referenced_names",
]
