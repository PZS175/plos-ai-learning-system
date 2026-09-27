"""内置插件：数理化公式手册。

中学常用公式静态库，分类浏览 + 关键词搜索：

- 右侧查看公式与说明，一键复制公式文本；
- 每条公式下方提供「🧮 去使用」按钮：自动列出公式中的物理量，
  填入数值即实时算出结果（多个输出式子分别显示，缺量的式子显示 —），
  可一键复制计算结果；
- 规律/定理类条目（如质量守恒定律）不提供代入计算，按钮会给出说明。

计算安全：复用共享的 ``plos.plugins.expr.ExprEvaluator``（ast 白名单，无 eval），
输入框本身也支持算式（如 ``2*3``、``pi/2``）。
"""

from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from plos.plugins.expr import ExprEvaluator, referenced_names

# (分类, 名称, 公式, 说明)
_FORMULAS: List[Tuple[str, str, str, str]] = [
    # ---------------- 数学 ----------------
    ("数学", "平方差公式", "a² - b² = (a+b)(a-b)", "因式分解与整式乘法的基本公式。"),
    ("数学", "完全平方公式", "(a±b)² = a² ± 2ab + b²", "两数和（差）的平方。"),
    ("数学", "一元二次方程求根", "x = (-b ± √(b²-4ac)) / 2a", "适用于 ax²+bx+c=0（a≠0）。"),
    ("数学", "判别式", "Δ = b² - 4ac", "Δ>0 两个不等实根；Δ=0 两个相等实根；Δ<0 无实根。"),
    ("数学", "韦达定理", "x₁+x₂ = -b/a，x₁·x₂ = c/a", "一元二次方程根与系数的关系。"),
    ("数学", "二次函数顶点", "(-b/2a, (4ac-b²)/4a)", "y=ax²+bx+c 的顶点坐标（对称轴 x=-b/2a）。"),
    ("数学", "等差数列", "aₙ = a₁+(n-1)d；Sₙ = n(a₁+aₙ)/2", "d 为公差。"),
    ("数学", "等比数列", "aₙ = a₁·qⁿ⁻¹；Sₙ = a₁(1-qⁿ)/(1-q)", "q 为公比，q≠1。"),
    ("数学", "勾股定理", "a² + b² = c²", "直角三角形两直角边与斜边的关系。"),
    ("数学", "三角形面积", "S = ½ah = ½ab·sinC", "a 为底 h 为高；或两边及其夹角。"),
    ("数学", "正弦定理", "a/sinA = b/sinB = c/sinC = 2R", "R 为外接圆半径。"),
    ("数学", "余弦定理", "c² = a² + b² - 2ab·cosC", "勾股定理的推广。"),
    ("数学", "圆的周长与面积", "C = 2πr；S = πr²", "r 为半径。"),
    ("数学", "扇形面积", "S = nπr²/360 = ½lr", "n 为圆心角度数，l 为弧长。"),
    ("数学", "梯形面积", "S = ½(a+b)h", "a、b 为上下底，h 为高。"),
    ("数学", "圆柱", "V = πr²h；S侧 = 2πrh", "r 为底面半径，h 为高。"),
    ("数学", "圆锥", "V = ⅓πr²h", "等底等高的圆锥体积是圆柱的 1/3。"),
    ("数学", "球", "V = 4/3·πr³；S = 4πr²", "r 为半径。"),
    ("数学", "方差", "s² = (1/n)·Σ(xᵢ-x̄)²", "衡量数据波动大小，方差越大越不稳定。"),
    # ---------------- 物理 ----------------
    ("物理", "匀速直线运动", "v = s/t", "速度 = 路程 / 时间。"),
    ("物理", "密度", "ρ = m/V", "密度 = 质量 / 体积，单位 kg/m³ 或 g/cm³。"),
    ("物理", "重力", "G = mg", "g 约取 9.8 N/kg（粗略计算取 10 N/kg）。"),
    ("物理", "压强", "p = F/S", "压强 = 压力 / 受力面积，单位帕斯卡 Pa。"),
    ("物理", "液体压强", "p = ρgh", "h 为从液面到该点的竖直深度。"),
    ("物理", "阿基米德原理", "F浮 = G排 = ρ液·g·V排", "浮力等于排开液体所受的重力。"),
    ("物理", "功", "W = Fs", "力与在力方向上移动距离的乘积，单位焦耳 J。"),
    ("物理", "功率", "P = W/t = Fv", "表示做功快慢，单位瓦特 W。"),
    ("物理", "杠杆平衡条件", "F₁L₁ = F₂L₂", "动力×动力臂 = 阻力×阻力臂。"),
    ("物理", "机械效率", "η = W有用/W总 ×100%", "η 总小于 1（或 100%）。"),
    ("物理", "物体吸放热", "Q = cmΔt", "c 为比热容，m 为质量，Δt 为温度变化。"),
    ("物理", "燃料放热", "Q = mq", "q 为燃料的热值。"),
    ("物理", "欧姆定律", "I = U/R", "导体中的电流与电压成正比、与电阻成反比。"),
    ("物理", "电功", "W = UIt = Pt", "单位焦耳 J，生活中常用 kW·h（度）。"),
    ("物理", "电功率", "P = UI = I²R = U²/R", "后两式仅适用于纯电阻电路。"),
    ("物理", "焦耳定律", "Q = I²Rt", "电流通过导体产生的热量。"),
    ("物理", "串联电路", "I=I₁=I₂；U=U₁+U₂；R=R₁+R₂", "串联分压，电流处处相等。"),
    ("物理", "并联电路", "U=U₁=U₂；I=I₁+I₂；1/R=1/R₁+1/R₂", "并联分流，各支路电压相等。"),
    ("物理", "波速", "v = λf", "波速 = 波长 × 频率；电磁波在真空中 c=3×10⁸ m/s。"),
    # ---------------- 化学 ----------------
    ("化学", "物质的量", "n = m/M = N/Nₐ", "M 为摩尔质量，Nₐ≈6.02×10²³ /mol。"),
    ("化学", "气体摩尔体积", "V = n·Vₘ", "标准状况下 Vₘ ≈ 22.4 L/mol。"),
    ("化学", "物质的量浓度", "c = n/V", "单位 mol/L，V 为溶液体积。"),
    ("化学", "溶质质量分数", "w = m(溶质)/m(溶液) ×100%", "m(溶液) = m(溶质) + m(溶剂)。"),
    ("化学", "稀释定律", "c浓·V浓 = c稀·V稀", "稀释前后溶质的物质的量不变。"),
    ("化学", "质量守恒定律", "Σm(反应物) = Σm(生成物)", "参加反应的各物质质量总和等于生成物质量总和。"),
    ("化学", "pH 值", "pH = -lg[H⁺]", "25 ℃ 时 pH<7 酸性，=7 中性，>7 碱性。"),
    ("化学", "理想气体状态方程", "PV = nRT", "R≈8.314 J/(mol·K)，T 为热力学温度。"),
]

# 每条公式的「代入计算」定义：变量（名, 说明, 示例值）与输出（标签, 计算式）
_USABLE: Dict[str, Dict[str, list]] = {
    # ---------------- 数学 ----------------
    "平方差公式": {
        "vars": [("a", "第一个数", "5"), ("b", "第二个数", "3")],
        "out": [("a² − b²", "a**2 - b**2"), ("(a+b)(a−b)", "(a+b)*(a-b)")],
    },
    "完全平方公式": {
        "vars": [("a", "第一个数", "5"), ("b", "第二个数", "3")],
        "out": [
            ("(a+b)²", "(a+b)**2"),
            ("(a−b)²", "(a-b)**2"),
            ("a² + b²", "a**2 + b**2"),
        ],
    },
    "一元二次方程求根": {
        "vars": [("a", "二次项系数 a", "1"), ("b", "一次项系数 b", "-3"), ("c", "常数项 c", "2")],
        "out": [("x₁", "(-b + sqrt(b**2 - 4*a*c)) / (2*a)"),
                ("x₂", "(-b - sqrt(b**2 - 4*a*c)) / (2*a)")],
    },
    "判别式": {
        "vars": [("a", "二次项系数 a", "1"), ("b", "一次项系数 b", "-3"), ("c", "常数项 c", "2")],
        "out": [("Δ", "b**2 - 4*a*c")],
    },
    "韦达定理": {
        "vars": [("a", "二次项系数 a", "1"), ("b", "一次项系数 b", "-3"), ("c", "常数项 c", "2")],
        "out": [("x₁ + x₂", "-b/a"), ("x₁ · x₂", "c/a")],
    },
    "二次函数顶点": {
        "vars": [("a", "二次项系数 a", "1"), ("b", "一次项系数 b", "-4"), ("c", "常数项 c", "3")],
        "out": [
            ("顶点横坐标", "-b/(2*a)"),
            ("顶点纵坐标", "(4*a*c - b**2)/(4*a)"),
            ("对称轴 x", "-b/(2*a)"),
        ],
    },
    "等差数列": {
        "vars": [("a1", "首项 a₁", "1"), ("d", "公差 d", "2"), ("n", "项数 n", "10")],
        "out": [("aₙ 第 n 项", "a1 + (n-1)*d"), ("Sₙ 前 n 项和", "n*(2*a1 + (n-1)*d)/2")],
    },
    "等比数列": {
        "vars": [("a1", "首项 a₁", "1"), ("q", "公比 q", "2"), ("n", "项数 n", "10")],
        "out": [("aₙ 第 n 项", "a1*q**(n-1)"), ("Sₙ 前 n 项和", "a1*(1-q**n)/(1-q)")],
    },
    "勾股定理": {
        "vars": [("a", "直角边 a", "3"), ("b", "直角边 b", "4")],
        "out": [("斜边 c", "sqrt(a**2 + b**2)")],
    },
    "三角形面积": {
        "vars": [
            ("a", "底 a（或一边）", "6"),
            ("h", "高 h", "4"),
            ("b", "另一边 b", "5"),
            ("C", "a 与 b 的夹角（度）", "30"),
        ],
        "out": [("S = ½ah", "a*h/2"), ("S = ½ab·sinC", "a*b*sin(C)/2")],
    },
    "正弦定理": {
        "vars": [("a", "边 a", "3"), ("A", "a 的对角（度）", "30")],
        "out": [("2R（外接圆直径）", "a/sin(A)")],
    },
    "余弦定理": {
        "vars": [("a", "边 a", "3"), ("b", "边 b", "4"), ("C", "a 与 b 的夹角（度）", "90")],
        "out": [("边 c", "sqrt(a**2 + b**2 - 2*a*b*cos(C))")],
    },
    "圆的周长与面积": {
        "vars": [("r", "半径 r", "2")],
        "out": [("周长 C", "2*pi*r"), ("面积 S", "pi*r**2")],
    },
    "扇形面积": {
        "vars": [("n", "圆心角 n（度）", "60"), ("r", "半径 r", "3"), ("l", "弧长 l", "3.14")],
        "out": [("S = nπr²/360", "n*pi*r**2/360"), ("S = ½lr", "l*r/2")],
    },
    "梯形面积": {
        "vars": [("a", "上底 a", "3"), ("b", "下底 b", "5"), ("h", "高 h", "4")],
        "out": [("面积 S", "(a+b)*h/2")],
    },
    "圆柱": {
        "vars": [("r", "底面半径 r", "2"), ("h", "高 h", "5")],
        "out": [("体积 V", "pi*r**2*h"), ("侧面积 S侧", "2*pi*r*h"), ("表面积 S全", "2*pi*r**2 + 2*pi*r*h")],
    },
    "圆锥": {
        "vars": [("r", "底面半径 r", "2"), ("h", "高 h", "6")],
        "out": [("体积 V", "pi*r**2*h/3")],
    },
    "球": {
        "vars": [("r", "半径 r", "2")],
        "out": [("体积 V", "4/3*pi*r**3"), ("表面积 S", "4*pi*r**2")],
    },
    "方差": {
        "vars": [],
        "out": [],
        # 需要一组数据，单独用数据输入框处理
        "dataset": ("data", "一组数据（空格或逗号分隔）", "1 2 3 4 5"),
        "dataset_out": [("平均数 x̄", "mean"), ("方差 s²", "variance"), ("标准差 s", "stdev")],
    },
    # ---------------- 物理 ----------------
    "匀速直线运动": {
        "vars": [("s", "路程 s", "100"), ("t", "时间 t", "20"), ("v", "速度 v", "5")],
        "out": [("速度 v = s/t", "s/t"), ("路程 s = vt", "v*t"), ("时间 t = s/v", "s/v")],
    },
    "密度": {
        "vars": [("m", "质量 m", "100"), ("V", "体积 V", "20"), ("rho", "密度 ρ", "5")],
        "out": [("密度 ρ = m/V", "m/V"), ("质量 m = ρV", "rho*V"), ("体积 V = m/ρ", "m/rho")],
    },
    "重力": {
        "vars": [("m", "质量 m（kg）", "10"), ("g", "g（N/kg）", "9.8"), ("G", "重力 G（N）", "98")],
        "out": [("重力 G = mg", "m*g"), ("质量 m = G/g", "G/g")],
    },
    "压强": {
        "vars": [("F", "压力 F（N）", "100"), ("S", "受力面积 S（m²）", "2"), ("p", "压强 p（Pa）", "50")],
        "out": [("压强 p = F/S", "F/S"), ("压力 F = pS", "p*S"), ("受力面积 S = F/p", "F/p")],
    },
    "液体压强": {
        "vars": [
            ("rho", "液体密度 ρ（kg/m³）", "1000"),
            ("g", "g（N/kg）", "9.8"),
            ("h", "深度 h（m）", "2"),
        ],
        "out": [("压强 p = ρgh", "rho*g*h")],
    },
    "阿基米德原理": {
        "vars": [
            ("rho", "液体密度 ρ液（kg/m³）", "1000"),
            ("g", "g（N/kg）", "9.8"),
            ("V", "排开体积 V排（m³）", "0.002"),
        ],
        "out": [("浮力 F浮 = ρ液gV排", "rho*g*V")],
    },
    "功": {
        "vars": [("F", "力 F（N）", "10"), ("s", "距离 s（m）", "5"), ("W", "功 W（J）", "50")],
        "out": [("功 W = Fs", "F*s"), ("力 F = W/s", "W/s"), ("距离 s = W/F", "W/F")],
    },
    "功率": {
        "vars": [
            ("W", "功 W（J）", "100"),
            ("t", "时间 t（s）", "10"),
            ("F", "力 F（N）", "20"),
            ("v", "速度 v（m/s）", "2"),
        ],
        "out": [("功率 P = W/t", "W/t"), ("功率 P = Fv", "F*v")],
    },
    "杠杆平衡条件": {
        "vars": [
            ("F1", "动力 F₁", "10"),
            ("L1", "动力臂 L₁", "2"),
            ("F2", "阻力 F₂", "5"),
            ("L2", "阻力臂 L₂", "4"),
        ],
        "out": [
            ("F₂ = F₁L₁/L₂", "F1*L1/L2"),
            ("L₂ = F₁L₁/F₂", "F1*L1/F2"),
            ("F₁ = F₂L₂/L₁", "F2*L2/L1"),
        ],
    },
    "机械效率": {
        "vars": [("Wu", "有用功 W有用（J）", "800"), ("Wt", "总功 W总（J）", "1000")],
        "out": [("机械效率 η（%）", "Wu/Wt*100")],
    },
    "物体吸放热": {
        "vars": [
            ("c", "比热容 c（J/(kg·℃)）", "4200"),
            ("m", "质量 m（kg）", "1"),
            ("dt", "温度变化 Δt（℃）", "10"),
            ("Q", "热量 Q（J）", "42000"),
        ],
        "out": [("热量 Q = cmΔt", "c*m*dt"), ("温度变化 Δt = Q/(cm)", "Q/(c*m)")],
    },
    "燃料放热": {
        "vars": [("m", "质量 m（kg）", "2"), ("q", "热值 q（J/kg）", "46000000")],
        "out": [("放热 Q = mq", "m*q")],
    },
    "欧姆定律": {
        "vars": [("U", "电压 U（V）", "6"), ("R", "电阻 R（Ω）", "3"), ("I", "电流 I（A）", "2")],
        "out": [("电流 I = U/R", "U/R"), ("电压 U = IR", "I*R"), ("电阻 R = U/I", "U/I")],
    },
    "电功": {
        "vars": [
            ("U", "电压 U（V）", "6"),
            ("I", "电流 I（A）", "2"),
            ("t", "时间 t（s）", "10"),
            ("P", "电功率 P（W）", "12"),
        ],
        "out": [("电功 W = UIt", "U*I*t"), ("电功 W = Pt", "P*t")],
    },
    "电功率": {
        "vars": [
            ("U", "电压 U（V）", "6"),
            ("I", "电流 I（A）", "2"),
            ("R", "电阻 R（Ω）", "3"),
        ],
        "out": [("P = UI", "U*I"), ("P = I²R", "I**2*R"), ("P = U²/R", "U**2/R")],
    },
    "焦耳定律": {
        "vars": [
            ("I", "电流 I（A）", "2"),
            ("R", "电阻 R（Ω）", "3"),
            ("t", "时间 t（s）", "10"),
        ],
        "out": [("热量 Q = I²Rt", "I**2*R*t")],
    },
    "串联电路": {
        "vars": [
            ("R1", "电阻 R₁（Ω）", "2"),
            ("R2", "电阻 R₂（Ω）", "3"),
            ("U1", "电压 U₁（V）", "2"),
            ("U2", "电压 U₂（V）", "3"),
        ],
        "out": [("总电阻 R = R₁+R₂", "R1+R2"), ("总电压 U = U₁+U₂", "U1+U2")],
    },
    "并联电路": {
        "vars": [("R1", "电阻 R₁（Ω）", "2"), ("R2", "电阻 R₂（Ω）", "3")],
        "out": [("总电阻 R", "1/(1/R1 + 1/R2)")],
    },
    "波速": {
        "vars": [
            ("lam", "波长 λ（m）", "2"),
            ("f", "频率 f（Hz）", "50"),
            ("v", "波速 v（m/s）", "100"),
        ],
        "out": [("波速 v = λf", "lam*f"), ("波长 λ = v/f", "v/f"), ("频率 f = v/λ", "v/lam")],
    },
    # ---------------- 化学 ----------------
    "物质的量": {
        "vars": [("m", "质量 m（g）", "18"), ("M", "摩尔质量 M（g/mol）", "18")],
        "out": [("物质的量 n = m/M（mol）", "m/M"), ("微粒数 N = n·NA", "m/M*6.02e23")],
    },
    "气体摩尔体积": {
        "vars": [
            ("n", "物质的量 n（mol）", "2"),
            ("Vm", "摩尔体积 Vₘ（L/mol）", "22.4"),
        ],
        "out": [("体积 V = n·Vₘ（L）", "n*Vm")],
    },
    "物质的量浓度": {
        "vars": [
            ("n", "溶质物质的量 n（mol）", "1"),
            ("V", "溶液体积 V（L）", "2"),
            ("c", "浓度 c（mol/L）", "0.5"),
        ],
        "out": [("浓度 c = n/V（mol/L）", "n/V"), ("物质的量 n = c·V（mol）", "c*V")],
    },
    "溶质质量分数": {
        "vars": [("ms", "溶质质量（g）", "10"), ("mz", "溶液质量（g）", "100")],
        "out": [("质量分数 w（%）", "ms/mz*100")],
    },
    "稀释定律": {
        "vars": [
            ("c1", "原浓度 c浓（mol/L）", "1"),
            ("V1", "原体积 V浓（L）", "0.1"),
            ("c2", "稀释后浓度 c稀（mol/L）", "0.5"),
            ("V2", "稀释后体积 V稀（L）", "0.2"),
        ],
        "out": [
            ("稀释后体积 V稀 = c浓V浓/c稀（L）", "c1*V1/c2"),
            ("稀释后浓度 c稀 = c浓V浓/V稀（mol/L）", "c1*V1/V2"),
            ("原体积 V浓 = c稀V稀/c浓（L）", "c2*V2/c1"),
        ],
    },
    "pH 值": {
        "vars": [("h", "c(H⁺)（mol/L）", "0.001")],
        "out": [("pH", "-log(h)")],
    },
    "理想气体状态方程": {
        "vars": [
            ("P", "压强 P（Pa）", "101325"),
            ("V", "体积 V（m³）", "0.0224"),
            ("n", "物质的量 n（mol）", "1"),
            ("T", "温度 T（K）", "273"),
            ("R", "常数 R", "8.314"),
        ],
        "out": [
            ("V = nRT/P", "n*R*T/P"),
            ("P = nRT/V", "n*R*T/V"),
            ("n = PV/(RT)", "P*V/(R*T)"),
        ],
    },
}


def _fmt(value: float) -> str:
    if value is None or not math.isfinite(value):
        return "无法计算"
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.6g}"


def _parse_dataset(text: str) -> List[float]:
    """把「1 2 3」或「1,2,3」解析为数值列表，非法片段跳过。"""
    values: List[float] = []
    for piece in re.split(r"[\s,，、;；]+", (text or "").strip()):
        if not piece:
            continue
        try:
            values.append(float(piece))
        except ValueError:
            continue
    return values


class FormulaBookPanel(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self._ctx = ctx
        self._colors = ctx.theme_colors()
        self._filtered: List[Tuple[str, str, str, str]] = list(_FORMULAS)
        self._var_edits: Dict[str, QLineEdit] = {}
        self._out_specs: List[Tuple[str, object, set]] = []
        self._dataset_edit: Optional[QLineEdit] = None
        self._dataset_out: List[Tuple[str, str]] = []
        self._use_visible = False
        self._build_ui()
        self._apply_filter("")

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        c = self._colors
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("数理化公式手册")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        head.addWidget(title)
        head.addStretch()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索公式名称 / 内容 / 关键词，如：勾股、压强、mol")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(320)
        self.search_edit.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 8px; padding: 8px 10px;"
        )
        self.search_edit.textChanged.connect(self._apply_filter)
        head.addWidget(self.search_edit)
        layout.addLayout(head)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.list_widget = QListWidget()
        self.list_widget.setMinimumWidth(240)
        self.list_widget.itemClicked.connect(self._show_detail)
        # 键盘上下键切换时也刷新详情
        self.list_widget.currentRowChanged.connect(self._on_row_changed)
        self.list_widget.setStyleSheet(
            f"QListWidget {{ background: {c.get('card_bg', '#fff')};"
            f" border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 12px;"
            f" color: {c.get('fg_primary', '#111827')}; padding: 4px; }}"
            f"QListWidget::item:selected {{ background: {c.get('accent_light', 'rgba(79,93,245,0.12)')}; }}"
        )
        splitter.addWidget(self.list_widget)

        detail_card = QFrame()
        detail_card.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 12px;"
        )
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(22, 20, 22, 20)
        detail_layout.setSpacing(12)

        self.detail_category = QLabel("")
        self.detail_category.setStyleSheet(
            f"color: {c.get('accent', '#4f5df5')}; font-size: 13px; font-weight: bold;"
        )
        detail_layout.addWidget(self.detail_category)

        self.detail_name = QLabel("选择左侧公式查看")
        self.detail_name.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 22px; font-weight: bold;"
        )
        detail_layout.addWidget(self.detail_name)

        self.formula_box = QTextBrowser()
        self.formula_box.setOpenExternalLinks(False)
        self.formula_box.setFrameShape(QFrame.Shape.NoFrame)
        self.formula_box.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')}; color: {c.get('fg_primary', '#111827')};"
            "border-radius: 10px; font-size: 20px; padding: 14px;"
        )
        self.formula_box.setMinimumHeight(96)
        self.formula_box.setMaximumHeight(150)
        detail_layout.addWidget(self.formula_box)

        # 公式下方的操作按钮
        action_row = QHBoxLayout()
        action_row.addStretch()
        self.use_btn = QPushButton("🧮 去使用")
        self.use_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.use_btn.setStyleSheet(
            f"background: {c.get('accent_light', 'rgba(79,93,245,0.12)')};"
            f"color: {c.get('accent', '#4f5df5')};"
            "border: none; border-radius: 8px; padding: 8px 18px; font-weight: bold;"
        )
        self.use_btn.clicked.connect(self._toggle_use_area)
        action_row.addWidget(self.use_btn)

        self.copy_btn = QPushButton("📋 复制公式")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setStyleSheet(
            f"background: {c.get('accent', '#4f5df5')}; color: white;"
            "border: none; border-radius: 8px; padding: 8px 18px; font-weight: bold;"
        )
        self.copy_btn.clicked.connect(self._copy_formula)
        action_row.addWidget(self.copy_btn)
        detail_layout.addLayout(action_row)

        # 代入计算区（默认隐藏，点「去使用」展开）
        self.use_frame = QFrame()
        self.use_frame.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')}; border-radius: 10px;"
        )
        use_layout = QVBoxLayout(self.use_frame)
        use_layout.setContentsMargins(16, 14, 16, 14)
        use_layout.setSpacing(8)

        self.use_hint = QLabel("把公式里的各量填成你的数值，结果会实时更新（输入框支持算式，如 2*3）。")
        self.use_hint.setWordWrap(True)
        self.use_hint.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;"
        )
        use_layout.addWidget(self.use_hint)

        self.use_form = QGridLayout()
        self.use_form.setHorizontalSpacing(10)
        self.use_form.setVerticalSpacing(6)
        self.use_form.setColumnStretch(1, 1)
        use_layout.addLayout(self.use_form)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.result_label.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 15px; font-weight: bold;"
        )
        use_layout.addWidget(self.result_label)

        result_row = QHBoxLayout()
        result_row.addStretch()
        self.result_copy_btn = QPushButton("📋 复制结果")
        self.result_copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.result_copy_btn.setStyleSheet(
            f"background: transparent; color: {c.get('accent', '#4f5df5')};"
            f"border: 1px solid {c.get('accent_border', 'rgba(79,93,245,0.45)')};"
            "border-radius: 8px; padding: 5px 12px;"
        )
        self.result_copy_btn.clicked.connect(self._copy_result)
        result_row.addWidget(self.result_copy_btn)
        use_layout.addLayout(result_row)

        self.use_frame.setVisible(False)
        detail_layout.addWidget(self.use_frame)

        self.note_label = QLabel("")
        self.note_label.setWordWrap(True)
        self.note_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 14px;"
        )
        detail_layout.addWidget(self.note_label)
        detail_layout.addStretch()
        splitter.addWidget(detail_card)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;"
        )
        layout.addWidget(self.count_label)

    # ------------------------------------------------------------------
    def _apply_filter(self, keyword: str) -> None:
        kw = keyword.strip().lower()
        if kw:
            self._filtered = [
                item
                for item in _FORMULAS
                if kw in item[0].lower()
                or kw in item[1].lower()
                or kw in item[2].lower()
                or kw in item[3].lower()
            ]
        else:
            self._filtered = list(_FORMULAS)

        self.list_widget.clear()
        for category, name, _formula, _note in self._filtered:
            item = QListWidgetItem(f"[{category}] {name}")
            item.setSizeHint(item.sizeHint())
            self.list_widget.addItem(item)
        self.count_label.setText(f"共 {len(self._filtered)} 条公式")
        if self._filtered:
            self.list_widget.setCurrentRow(0)
            self._show_detail(self.list_widget.currentItem())
        else:
            self.detail_name.setText("没有匹配的公式")
            self.formula_box.setText("")
            self.note_label.setText("换个关键词试试。")
            self.detail_category.setText("")
            self._clear_use_area()

    def _current_formula(self) -> Optional[Tuple[str, str, str, str]]:
        row = self.list_widget.currentRow()
        if 0 <= row < len(self._filtered):
            return self._filtered[row]
        return None

    def _on_row_changed(self, _row: int) -> None:
        self._show_detail()

    def _show_detail(self, _item: Optional[QListWidgetItem] = None) -> None:
        item = self._current_formula()
        if item is None:
            return
        category, name, formula, note = item
        self.detail_category.setText(category)
        self.detail_name.setText(name)
        self.formula_box.setText(f"<div style='text-align:center'>{formula}</div>")
        self.note_label.setText(note)
        self._rebuild_use_area(name)

    # ------------------------------------------------------------------
    # 「去使用」：代入计算
    # ------------------------------------------------------------------
    def _clear_use_area(self) -> None:
        while self.use_form.count():
            child = self.use_form.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()
        self._var_edits = {}
        self._out_specs = []
        self._dataset_edit = None
        self._dataset_out = []
        self.result_label.setText("")

    def _rebuild_use_area(self, name: str) -> None:
        self._clear_use_area()
        spec = _USABLE.get(name)
        if not spec:
            self.use_frame.setVisible(False)
            self._use_visible = False
            self.use_btn.setText("🧮 去使用")
            self.use_btn.setEnabled(False)
            self.use_btn.setToolTip("该条为规律 / 定理表述，暂不支持自动代入计算")
            return

        self.use_btn.setEnabled(True)
        self.use_btn.setToolTip("填入各量数值，实时算出结果")
        c = self._colors
        evaluator_vars = {var for var, _desc, _default in spec["vars"]}
        row = 0

        for var, desc, default in spec["vars"]:
            label = QLabel(f"{var}　{desc}")
            label.setStyleSheet(
                f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;"
            )
            edit = QLineEdit(default)
            edit.setStyleSheet(
                f"background: {c.get('card_bg', '#fff')};"
                f"color: {c.get('fg_primary', '#111827')};"
                f"border: 1px solid {c.get('border', '#e5e7eb')};"
                "border-radius: 6px; padding: 5px 8px;"
            )
            edit.textChanged.connect(self._recompute)
            self.use_form.addWidget(label, row, 0)
            self.use_form.addWidget(edit, row, 1)
            self._var_edits[var] = edit
            row += 1

        # 需要一组数据的公式（如方差）
        dataset = spec.get("dataset")
        if dataset:
            _key, desc, default = dataset
            label = QLabel(desc)
            label.setStyleSheet(
                f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;"
            )
            edit = QLineEdit(default)
            edit.setStyleSheet(
                f"background: {c.get('card_bg', '#fff')};"
                f"color: {c.get('fg_primary', '#111827')};"
                f"border: 1px solid {c.get('border', '#e5e7eb')};"
                "border-radius: 6px; padding: 5px 8px;"
            )
            edit.textChanged.connect(self._recompute)
            self.use_form.addWidget(label, row, 0)
            self.use_form.addWidget(edit, row, 1)
            self._dataset_edit = edit
            self._dataset_out = list(spec.get("dataset_out", []))

        self._out_specs = []
        for label_text, expression in spec["out"]:
            try:
                node = ExprEvaluator(variables=evaluator_vars, degrees=True).compile(expression)
            except Exception:
                continue
            self._out_specs.append((label_text, node, referenced_names(node)))

        self.use_frame.setVisible(self._use_visible)
        self._recompute()

    def _toggle_use_area(self) -> None:
        if not self._var_edits and self._dataset_edit is None:
            return
        self._use_visible = not self._use_visible
        self.use_frame.setVisible(self._use_visible)
        self.use_btn.setText("🧮 收起计算" if self._use_visible else "🧮 去使用")
        if self._use_visible:
            self._recompute()

    def _recompute(self) -> None:
        if not self._out_specs and self._dataset_edit is None:
            return
        evaluator = ExprEvaluator(variables=set(self._var_edits), degrees=True)
        values: Dict[str, Optional[float]] = {}
        for var, edit in self._var_edits.items():
            text = edit.text().strip()
            if not text:
                values[var] = None
                continue
            try:
                values[var] = evaluator.eval(text)
            except Exception:
                values[var] = None

        lines: List[str] = []
        for label_text, node, names in self._out_specs:
            if any(values.get(name) is None for name in names):
                lines.append(f"{label_text} = —（补齐上面各项）")
                continue
            try:
                result = evaluator.eval_compiled(
                    node, **{name: values[name] for name in names if values.get(name) is not None}
                )
            except Exception:
                lines.append(f"{label_text} = 无法计算（检查取值）")
                continue
            lines.append(f"{label_text} = {_fmt(result)}")

        if self._dataset_edit is not None:
            lines.extend(self._dataset_lines())

        self.result_label.setText("\n".join(lines))

    def _dataset_lines(self) -> List[str]:
        """按一组数据算平均数 / 方差 / 标准差（总体方差，与教材一致）。"""
        data = _parse_dataset(self._dataset_edit.text())
        if len(data) < 2:
            return ["统计结果 = —（请输入至少 2 个数据）"]
        count = len(data)
        mean = sum(data) / count
        variance = sum((value - mean) ** 2 for value in data) / count
        stats = {"mean": mean, "variance": variance, "stdev": math.sqrt(variance)}
        return [
            f"{label_text} = {_fmt(stats.get(key))}"
            for label_text, key in self._dataset_out
        ]

    def _copy_result(self) -> None:
        text = self.result_label.text().strip()
        if not text:
            return
        QApplication.clipboard().setText(f"{self.detail_name.text()}：\n{text}")
        self.result_copy_btn.setText("✓ 已复制")
        QTimer.singleShot(1200, lambda: self.result_copy_btn.setText("📋 复制结果"))

    def _copy_formula(self) -> None:
        item = self._current_formula()
        if item is None:
            return
        QApplication.clipboard().setText(item[2])
        self.copy_btn.setText("✓ 已复制")
        QTimer.singleShot(1200, lambda: self.copy_btn.setText("📋 复制公式"))


def register(ctx) -> None:
    ctx.register_panel(
        "main",
        "公式手册",
        lambda c: FormulaBookPanel(c),
        icon="📖",
        subtitle="中学数理化常用公式速查，支持代入计算",
    )
