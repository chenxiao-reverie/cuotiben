#!/usr/bin/env python3
"""错题本结构校验器。

用法:
    python3 check_notebook.py <错题本.md>

校验项:
    1. front matter 字段齐全且为数字（structure_rev 可选，有则须为数字）
    2. 模块数、错题数、知识点数与 front matter 一致
    3. Q 编号连续无跳号、无重复
    4. 每道错题的必填字段齐全
    5. 错因 7 项、掌握度 3 档的复选框数量正确（加粗项也算一项）
    6. 全文件复选框写法统一（默认单行 ☐，也可整份竖排 - [ ]）
    7. 选项拆行后每行带 A. B. 标号，且至少两项
    8. 「考点」引用的知识点 ID 在正文中有定义
    9. 必备章节存在，变化信息段存在
   10. 模块内的分组标题没有误用 ### 级

另有两类「该换写法了」的聚合提醒，只是警告不阻断：
   · 选项挤在一行没拆开
   · 知识点还在用单行竖线写法

通过后额外输出「结构健康度」：模块数、最重模块、总题数、M99 积压。
健康度只提示不阻断，退出码不受影响。

关于标注: 单行写法里框点不动，用户靠把某项加粗来标记选中，所以
「已选」的判定是 ☑ 或加粗，两者同等计入。用户手标的 ☑ / [x] / 加粗
都是正常资产，不做报错——「生成时不预勾」是 AI 的写作纪律，由 SKILL.md
约束，不在此处判定。

退出码: 0 通过（可含警告），1 有错误。
"""

import re
import sys
from pathlib import Path

FRONT_KEYS = ["title", "updated", "modules", "questions", "points", "parse_missing"]
NUM_KEYS = ["modules", "questions", "points", "parse_missing"]
OPT_NUM_KEYS = ["structure_rev"]

REQ_FIELDS = [
    "考点",
    "题干",
    "我的答案",
    "解析要点",
    "错因",
    "错因依据",
    "正解思路",
    "方法沉淀",
    "一句话提醒",
    "掌握度",
]
OPT_FIELDS = ["选项"]

ERR_TYPE_COUNT = 7
MASTER_COUNT = 3

REQ_SECTIONS = [
    "# 目录",
    "## 通用方法索引",
    "## M99 待归类",
    "## 重复项清单",
    "## 待人工确认",
    "## 解析缺失清单",
    "## 整理说明",
]

# 结构健康度阈值（只提示，不阻断）
TH_MODULES = 10        # 模块过多
TH_MODULE_Q = 20       # 单模块过重
TH_TOTAL_Q = 80        # 总量过大
TH_M99 = 5             # 待归类积压

CHECKLINE_RE = re.compile(r"^\s*-\s*\[[ xX]\]\s*\S")
OPT_LINE_RE = re.compile(r"^\s*-\s*[A-H][.．、)）:：]\s*\S")
INLINE_OPT_RE = re.compile(r"[A-H][.．、)）]\s*\S")
POINT_LINE_RE = re.compile(r"^\s*[-*]\s*\[K-[A-Z0-9]+-\d+\]")


def field_pat(name: str):
    """匹配 `- **字段**：值` 形式，允许标签加粗（排版用），冒号前后空白不限。"""
    return re.compile(rf"^\s*[-*]\s*\**{re.escape(name)}\**\s*[：:]\s*(.*)$")

errors: list[str] = []
warns: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warns.append(msg)


def split_front(text: str):
    """返回 (frontmatter dict, 正文, 是否找到 front matter)"""
    if not text.startswith("---"):
        return {}, text, False
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not m:
        return {}, text, False
    fm_raw = m.group(1)
    body = text[m.end():]
    fm = {}
    for line in fm_raw.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, body, True


def check_front(fm: dict, found: bool) -> None:
    if not found:
        err("缺少 YAML front matter（文件应以 --- 开头）")
        return
    for k in FRONT_KEYS:
        if k not in fm:
            err(f"front matter 缺少字段: {k}")
    for k in NUM_KEYS + OPT_NUM_KEYS:
        if k in fm and not re.fullmatch(r"\d+", fm[k]):
            err(f"front matter `{k}` 必须是纯数字，当前为 `{fm[k]}`")
    if "updated" in fm and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fm["updated"]):
        warn(f"front matter `updated` 建议用 YYYY-MM-DD，当前为 `{fm['updated']}`")


def get_section(body: str, heading: str) -> str:
    """取某个 ## 或 # 标题到下一个同级或更高级标题之间的内容"""
    level = len(heading) - len(heading.lstrip("#"))
    pat = re.compile(rf"^{re.escape(heading)}\s*$", re.M)
    m = pat.search(body)
    if not m:
        return ""
    start = m.end()
    nxt = re.compile(rf"^#{{1,{level}}}\s+\S", re.M)
    m2 = nxt.search(body, start)
    return body[start: m2.start() if m2 else len(body)]


def check_sections(body: str) -> None:
    for s in REQ_SECTIONS:
        if not re.search(rf"^{re.escape(s)}\s*$", body, re.M):
            err(f"缺少必备章节: {s}")
    if "错题本变化信息" not in body:
        err("缺少「错题本变化信息」段")


def parse_questions(body: str):
    """返回 [(qid:int, 标题, 块正文)]"""
    pat = re.compile(r"^###\s+Q-(\d+)\s*·\s*(.*)$", re.M)
    hits = list(pat.finditer(body))
    out = []
    for i, m in enumerate(hits):
        start = m.end()
        end = hits[i + 1].start() if i + 1 < len(hits) else len(body)
        # 遇到下一个 ## 级标题也截断
        seg = body[start:end]
        h2 = re.search(r"^##\s+\S", seg, re.M)
        if h2:
            seg = seg[: h2.start()]
        out.append((int(m.group(1)), m.group(2).strip(), seg))
    return out


def field_lines(block: str) -> dict:
    """抽取 `- 字段名：值` 形式（标签可加粗），返回 {字段名: 值}（重复取首个）"""
    d = {}
    for line in block.splitlines():
        m = re.match(r"^\s*[-*]\s*([^：:]{1,24})[：:]\s*(.*)$", line)
        if m:
            key = m.group(1).strip().strip("*").strip()
            d.setdefault(key, m.group(2).strip())
    return d


def match_field(d: dict, name: str):
    for k in d:
        if k.startswith(name):
            return d[k]
    return None


def parse_marks(val: str):
    """统计单行写法里的框数与已选数。

    已选：项内含 ☑，或该项被加粗。加粗是给不可点的单行写法准备的替代标记，
    用户直接加粗而不动 ☐ 也能被读到。
    项内若既无框也无 ☑、但有加粗，仍算一项——覆盖「把 ☐ 删掉只留加粗」的写法。
    返回 (框数, 已选数)。
    """
    boxes = 0
    selected = 0
    for tok in re.split(r"　+", val.strip()):
        if not tok.strip():
            continue
        n = tok.count("☐") + tok.count("☑")
        is_bold = "**" in tok
        if n == 0 and is_bold:
            n = 1
        boxes += n
        if "☑" in tok or is_bold:
            selected += 1
    return boxes, selected


def box_style_and_count(block: str, field: str):
    """判定某字段用的写法并数出复选框总数。返回 (style, 框数, 已选数)。"""
    pat = field_pat(field)
    lines = block.splitlines()
    for i, line in enumerate(lines):
        m = pat.match(line)
        if not m:
            continue
        val = m.group(1).strip()
        boxes, selected = parse_marks(val)
        if boxes:
            return "inline", boxes, selected
        k = 0
        checked = 0
        for l in lines[i + 1:]:
            if CHECKLINE_RE.match(l):
                k += 1
                if re.search(r"\[[xX]\]", l):
                    checked += 1
            elif l.strip() == "":
                continue
            else:
                break
        return "vertical", k, checked
    return None, 0, 0


def detect_file_style(questions):
    """按全文件多数卡确定主写法；平票时取第一张卡的写法（保旧文件兼容）"""
    styles = []
    for _, _, block in questions:
        s, n, _ = box_style_and_count(block, "错因")
        if s and n:
            styles.append(s)
    if not styles:
        return None
    v, i = styles.count("vertical"), styles.count("inline")
    if v == i:
        return styles[0]
    return "vertical" if v > i else "inline"


STYLE_NAME = {"inline": "单行 ☐", "vertical": "竖排 - [ ]"}


def option_info(block: str):
    """返回 (style, 选项个数)。style 为 'inline' / 'vertical' / None。"""
    pat = field_pat("选项")
    lines = block.splitlines()
    for i, line in enumerate(lines):
        m = pat.match(line)
        if not m:
            continue
        val = m.group(1).strip()
        if val and val not in ("无", "无。", "略", "—"):
            return "inline", len(INLINE_OPT_RE.findall(val))
        n = 0
        for l in lines[i + 1:]:
            if OPT_LINE_RE.match(l):
                n += 1
            elif l.strip() == "":
                continue
            else:
                break
        return "vertical", n
    return None, 0


def check_question(qid: int, title: str, block: str, file_style, crammed: list, marked: dict) -> None:
    tag = f"Q-{qid:02d}"
    d = field_lines(block)

    if not title:
        warn(f"{tag} 标题为空，建议写一句话说清错在哪")

    for f in REQ_FIELDS:
        if match_field(d, f) is None:
            err(f"{tag} 缺少字段「{f}」")

    for fname, want in (("错因", ERR_TYPE_COUNT), ("掌握度", MASTER_COUNT)):
        has_field = match_field(d, fname) is not None
        style, got, sel = box_style_and_count(block, fname)
        if not has_field and got == 0:
            continue
        if style is None:
            err(f"{tag} 「{fname}」未识别到复选框（应 {want} 个）")
            continue
        if got != want:
            hint = "（七项/三档必须齐全，逐项核对漏了哪个）" if got < want else ""
            err(f"{tag} 「{fname}」复选框应 {want} 个，实际 {got} 个{hint}")
        if sel:
            marked[fname].append(qid)
        if file_style and style != file_style:
            err(
                f"{tag} 「{fname}」写法与全文件不一致"
                f"（全文件主写法：{STYLE_NAME[file_style]}；此处：{STYLE_NAME[style]}）"
            )

    # 选项：拆行后每行应带 A. B. 标号，且至少两项
    opt_style, n_opts = option_info(block)
    if opt_style == "vertical" and n_opts < 2:
        err(f"{tag} 「选项」拆行后应有 ≥2 项，实际 {n_opts} 项")
    elif opt_style == "inline" and n_opts >= 2:
        crammed.append(tag)

    # 正解思路步数
    steps = re.findall(r"^\s{2,}\d+[.、)]\s+\S", block, re.M)
    if len(steps) > 5:
        warn(f"{tag} 「正解思路」{len(steps)} 步，规范是 ≤5 步")


def check_ids(body: str, questions) -> int:
    def_ids = set(
        re.findall(r"^\s*(?:[-*]\s*)?(\[K-[A-Z0-9]+-\d+\])", body, re.M)
    )
    ref_ids = set()
    pat = field_pat("考点")
    for _, _, block in questions:
        for line in block.splitlines():
            m = pat.match(line)
            if m:
                ref_ids.update(re.findall(r"\[K-[A-Z0-9]+-\d+\]", m.group(1)))
    missing = sorted(ref_ids - def_ids)
    for mid in missing:
        err(f"「考点」引用了未定义的知识点 {mid}")
    unused = sorted(def_ids - ref_ids)
    if unused:
        warn(f"有 {len(unused)} 条知识点没有错题引用（若非孤儿知识点请检查）: {', '.join(unused[:5])}")
    return len(def_ids)


def module_segments(body: str):
    """返回 [(模块号, 名称, 段正文)]，M99 除外"""
    heads = list(re.finditer(r"^##\s+(M(?!99\b)\d+)\s+(.+)$", body, re.M))
    out = []
    for i, m in enumerate(heads):
        start = m.end()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        seg = body[start:end]
        nxt = re.search(r"^##\s+", seg, re.M)
        if nxt:
            seg = seg[: nxt.start()]
        out.append((m.group(1), m.group(2).strip(), seg))
    return out


def check_grouping_headings(segments) -> None:
    """模块内分组应用 ####；### 会打断 Q 卡的解析边界，把下一题字段吞进上一题"""
    for _, _, seg in segments:
        for m in re.finditer(r"^###\s+(.+)$", seg, re.M):
            title = m.group(1).strip()
            if re.match(r"^Q-\d+", title) or title in ("知识点", "错题"):
                continue
            warn(f"检测到模块内 ### 级标题「{title}」，分组请改用 ####")


def check_point_format(body: str) -> None:
    """知识点应为块状：结论一行 + 识别特征/易错点/来源 各一行。"""
    legacy = 0
    for line in body.splitlines():
        if POINT_LINE_RE.match(line) and line.count("|") >= 3:
            legacy += 1
    if legacy:
        warn(
            f"{legacy} 条知识点仍是单行竖线写法，"
            "建议改成块状（识别特征 / 易错点 / 来源 各占一行，见 card-schema）"
        )


def count_parse_missing(body: str) -> int:
    sec = get_section(body, "## 解析缺失清单")
    if not sec or "本期无" in sec:
        return 0
    return len(re.findall(r"^\|\s*Q-\d+", sec, re.M))


def module_stats(body: str):
    """返回 [(模块号, 名称, 题数)]"""
    out = []
    for mid, name, seg in module_segments(body):
        n = len(re.findall(r"^###\s+Q-\d+", seg, re.M))
        out.append((mid, name, n))
    return out


def m99_backlog(body: str) -> int:
    sec = get_section(body, "## M99 待归类")
    if not sec or "本期无" in sec:
        return 0
    rows = [
        l for l in sec.splitlines()
        if l.strip().startswith("|")
        and not re.match(r"^\|\s*[-:]+", l.strip())
        and "题号" not in l
    ]
    if rows:
        return len(rows)
    return len(re.findall(r"^\s*[-*]\s+\S", sec, re.M))


def print_health(module_count: int, q_count: int, stats, m99: int) -> None:
    print("结构健康度:")
    print(
        f"  模块 {module_count} 个（提示阈值 {TH_MODULES}）"
        f" · 错题 {q_count} 道（提示阈值 {TH_TOTAL_Q}）"
    )
    if stats:
        mid, name, n = max(stats, key=lambda s: s[2])
        print(f"  最重模块 {mid} {name} {n} 题（提示阈值 {TH_MODULE_Q}）")
    print(f"  M99 待归类 {m99} 条（提示阈值 {TH_M99}）")

    tips = []
    if stats:
        for mid, name, n in sorted(stats, key=lambda s: -s[2]):
            if n > TH_MODULE_Q:
                tips.append(f"{mid} {name} {n} 题，建议评估拆模块")
        fragile = [s for s in stats if s[2] <= 1]
        if len(fragile) >= 3:
            tips.append(f"{len(fragile)} 个模块只有 ≤1 题，建议评估合并")
    if module_count > TH_MODULES:
        tips.append(f"模块 {module_count} 个超过 {TH_MODULES}，建议评估合并或加层级")
    if q_count > TH_TOTAL_Q:
        tips.append(f"总题数 {q_count} 超过 {TH_TOTAL_Q}，建议评估整体切分")
    if m99 > TH_M99:
        tips.append(f"待归类积压 {m99} 条超过 {TH_M99}，说明分类体系有洞")

    if tips:
        for t in tips:
            print(f"  ⚠ {t}")
        print("  → 说「重排结构」可生成重构提案，不要擅自改")
    else:
        print("  ✓ 结构轻量，无需重构")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    path = Path(sys.argv[1]).expanduser()
    if not path.exists():
        print(f"✗ 文件不存在: {path}")
        return 1
    text = path.read_text(encoding="utf-8")

    fm, body, found = split_front(text)
    check_front(fm, found)
    check_sections(body)

    questions = parse_questions(body)
    file_style = detect_file_style(questions)

    crammed: list = []
    marked: dict = {"错因": [], "掌握度": []}
    for qid, title, block in questions:
        check_question(qid, title, block, file_style, crammed, marked)
    if crammed:
        shown = ", ".join(crammed[:5]) + ("…" if len(crammed) > 5 else "")
        warn(f"{len(crammed)} 道题的选项挤在一行（{shown}），建议每个选项单独一行")

    ids = [q[0] for q in questions]
    if ids:
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            err(f"题号重复: {', '.join(f'Q-{d:02d}' for d in sorted(dup))}")
        expect = list(range(1, max(ids) + 1))
        gaps = sorted(set(expect) - set(ids))
        if gaps:
            err(f"题号跳号，缺: {', '.join(f'Q-{g:02d}' for g in gaps)}")

    point_count = check_ids(body, questions)
    check_point_format(body)
    segments = module_segments(body)
    check_grouping_headings(segments)

    module_count = len(re.findall(r"^##\s+M(?!99\b)\d+\s+\S", body, re.M))
    pm = count_parse_missing(body)
    stats = module_stats(body)
    m99 = m99_backlog(body)

    actual = {
        "modules": module_count,
        "questions": len(questions),
        "points": point_count,
        "parse_missing": pm,
    }
    for k, v in actual.items():
        if k in fm and re.fullmatch(r"\d+", fm[k]):
            if int(fm[k]) != v:
                err(f"front matter `{k}` 写的是 {fm[k]}，正文实际 {v}")

    # 输出
    print(f"校验对象: {path.name}")
    print(f"  模块 {module_count} / 错题 {len(questions)} / 知识点 {point_count} / 解析缺失 {pm}")
    if file_style:
        print(f"  复选框写法: {STYLE_NAME[file_style]}")
    total_q = len(questions)
    if marked["错因"] or marked["掌握度"]:
        print(
            f"  标注进度: 错因 {len(marked['错因'])}/{total_q} 题"
            f" · 掌握度 {len(marked['掌握度'])}/{total_q} 题"
        )
    for w in warns:
        print(f"  ⚠ {w}")
    if errors:
        print(f"\n✗ 发现 {len(errors)} 个错误:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("\n✓ 结构校验通过\n")
    print_health(module_count, len(questions), stats, m99)
    return 0


if __name__ == "__main__":
    sys.exit(main())
