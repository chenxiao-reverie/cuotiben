#!/usr/bin/env python3
"""错题本结构校验器（v2 格式）。

用法:
    python3 check_notebook.py <错题本.md>

v2 骨架:
    YAML front matter → 首屏引用块 → 目录 → 零、模块总览与错因诊断
    → 模块一…N（N.1 知识点总结 / N.2 错题）→ 附录 A~D（可选）→ 附录 E 整理留痕

校验项:
    1. front matter 字段齐全且为数字
    2. 首屏引用块的题量/模块数/日期与 front matter 一致
    3. 必选章节存在：目录、零、模块、附录 E
    4. 模块标题序号连续，错题号全局连续无重复
    5. 知识点 K 号的模块位与所在模块一致，且不与错题引用脱节
    6. 总览表逐模块错题数与正文实际一致
    7. 错因分布与卡片里实际标注的数量一致
    8. 每题必填字段齐全，错因 7 项、掌握度 3 档的复选框数量正确
    9. 全文件复选框写法统一（单行 ☐ / 竖排 - [ ]）
   10. 选项形态合法：辨析表（有判定列）或列表（≥2 行带标号）

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

REQ_FIELDS = ["考点", "题干", "答案", "易错点", "一句结论", "错因", "错因依据", "掌握度"]

ERR_TYPE_COUNT = 7
MASTER_COUNT = 3

CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}

# 结构健康度阈值（只提示，不阻断）
TH_MODULES = 12       # 模块过多
TH_MODULE_Q = 20      # 单模块过重
TH_TOTAL_Q = 120      # 总量过大

CHECKLINE_RE = re.compile(r"^\s*-\s*\[[ xX]\]\s*\S")
OPT_LINE_RE = re.compile(r"^\s*-\s*[A-H][.．、)）:：]\s*\S")
OPT_ROW_RE = re.compile(r"^\|\s*([A-H])\s*\|(.+)$")

errors: list[str] = []
warns: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warns.append(msg)


def cn2int(s: str):
    """中文序号转整数，支持 一~十六。"""
    if not s:
        return None
    if s == "十":
        return 10
    if "十" in s:
        a, b = s.split("十", 1)
        h = CN_DIGITS.get(a, 1) if a else 1
        t = CN_DIGITS.get(b, 0) if b else 0
        return h * 10 + t
    return CN_DIGITS.get(s)


def split_front(text: str):
    if not text.startswith("---"):
        return {}, text, False
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not m:
        return {}, text, False
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, text[m.end():], True


def field_pat(name: str):
    """匹配 `- **字段**：值` 或 `**字段**　值`，允许标签加粗。"""
    return re.compile(rf"^\s*(?:[-*]\s*)?\**{re.escape(name)}\**\s*[：:　]\s*(.*)$")


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
        err(f"front matter `updated` 必须是 YYYY-MM-DD，当前为 `{fm['updated']}`")


def check_header(text: str, fm: dict) -> None:
    """首屏引用块的统计必须与 front matter 一致，防止改了 YAML 忘了改头。"""
    head = text[:2000]
    checks = [
        (r"题量\**\s*[：:]\s*(\d+)\s*题", "questions", "题量"),
        (r"模块\**\s*[：:]\s*(\d+)\s*个", "modules", "模块数"),
        (r"整理日期\**\s*[：:]\s*(\d{4}-\d{2}-\d{2})", "updated", "整理日期"),
    ]
    for pat, key, label in checks:
        m = re.search(pat, head)
        if not m:
            continue
        if key not in fm:
            continue
        if m.group(1) != fm[key]:
            err(f"首屏{label}写的是 {m.group(1)}，front matter 是 {fm[key]}，两处必须一致")


def parse_modules(body: str):
    """返回 [(序号, 序号原文, 名称, 段正文)]"""
    heads = list(re.finditer(r"^##\s+模块([一二三四五六七八九十]+)[ 　]+(.+)$", body, re.M))
    out = []
    for i, m in enumerate(heads):
        start = m.end()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        seg = body[start:end]
        nxt = re.search(r"^##\s+", seg, re.M)
        if nxt:
            seg = seg[: nxt.start()]
        out.append((cn2int(m.group(1)), m.group(1), m.group(2).strip(), seg))
    return out


def parse_questions(body: str):
    """返回 [(错题号, 标题, 块正文)]，块截到下一个 #### 或 ## 标题"""
    pat = re.compile(r"^####\s+错题\s*(\d+)\s*[｜|]\s*(.+)$", re.M)
    hits = list(pat.finditer(body))
    out = []
    for i, m in enumerate(hits):
        start = m.end()
        end = hits[i + 1].start() if i + 1 < len(hits) else len(body)
        seg = body[start:end]
        cut = re.search(r"^#{2,4}\s+\S", seg, re.M)
        if cut:
            seg = seg[: cut.start()]
        out.append((int(m.group(1)), m.group(2).strip(), seg))
    return out


def parse_marks(val: str):
    """返回 (框数, 已选标签列表)。已选 = 项含 ☑ 或该项加粗。"""
    boxes = 0
    picked = []
    for tok in re.split(r"　+", val.strip()):
        if not tok.strip():
            continue
        n = tok.count("☐") + tok.count("☑")
        is_bold = "**" in tok
        if n == 0 and is_bold:
            n = 1
        boxes += n
        label = re.sub(r"[☐☑*]", "", tok).strip()
        if "☑" in tok or is_bold:
            picked.append(label)
    return boxes, picked


def box_style_and_count(block: str, field: str):
    """返回 (style, 框数, 已选标签)。style: 'inline' / 'vertical' / None"""
    pat = field_pat(field)
    lines = block.splitlines()
    for i, line in enumerate(lines):
        m = pat.match(line)
        if not m:
            continue
        val = m.group(1).strip()
        boxes, picked = parse_marks(val)
        if boxes:
            return "inline", boxes, picked
        k = 0
        picked = []
        for l in lines[i + 1:]:
            if CHECKLINE_RE.match(l):
                k += 1
                label = re.sub(r"^\s*-\s*\[[ xX]\]\s*", "", l).strip()
                if re.search(r"\[[xX]\]", l):
                    picked.append(label)
            elif l.strip() == "":
                continue
            else:
                break
        return "vertical", k, picked
    return None, 0, []


def detect_file_style(questions):
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


def option_check(block: str):
    """返回 ('table'|'list'|None, 选项数, 问题描述或 None)"""
    rows = [l for l in block.splitlines() if OPT_ROW_RE.match(l)]
    if len(rows) >= 2:
        bad = []
        for r in rows:
            cells = [c.strip() for c in r.strip().strip("|").split("|")]
            if len(cells) < 3:
                bad.append(cells[0])
                continue
            if "✅" not in cells[-1] and "❌" not in cells[-1]:
                bad.append(cells[0])
        if bad:
            return "table", len(rows), f"判定列缺少 ✅/❌：{'、'.join(bad[:4])}"
        return "table", len(rows), None

    lines = [l for l in block.splitlines() if OPT_LINE_RE.match(l)]
    if lines:
        return "list", len(lines), None if len(lines) >= 2 else "列表形态至少两项"
    return None, 0, None


STYLE_NAME = {"inline": "单行 ☐", "vertical": "竖排 - [ ]"}


def clean_label(line: str) -> str:
    """把 `- **⚠️ 易错点**` 洗成 `易错点` 形式，方便按字段名定位。"""
    s = line.strip()
    s = re.sub(r"^[-*]\s*", "", s)
    s = s.replace("*", "")
    s = re.sub(r"^[\ufe0f\ufe0e⚠❗🔺▲\s]+", "", s)
    return s.strip()


def block_field(block: str, name: str):
    """取块内某字段标签后的内容；标签后可跟 ：/: 或全角空格。找不到返回 None。"""
    for line in block.splitlines():
        c = clean_label(line)
        if not c.startswith(name):
            continue
        nxt = c[len(name):len(name) + 1]
        if nxt and nxt not in "：:　 —–":
            continue
        return c[len(name):].lstrip("*：:　 —–")
    return None


def check_question(n: int, title: str, block: str, file_style, crammed: list, mark_log: dict) -> None:
    tag = f"错题 {n:02d}"

    for f in REQ_FIELDS:
        if block_field(block, f) is None:
            err(f"{tag} 缺少字段「{f}」")

    # 选项
    opt_style, n_opt, prob = option_check(block)
    if prob:
        err(f"{tag} 「选项」{prob}")
    if opt_style == "list" and n_opt:
        for l in block.splitlines():
            if OPT_LINE_RE.match(l) and re.search(r"[A-H][.．、)）]\s*\S.*[A-H][.．、)）]\s*\S", l):
                crammed.append(tag)
                break

    # 错因 / 掌握度
    marked_any = False
    for fname, want in (("错因", ERR_TYPE_COUNT), ("掌握度", MASTER_COUNT)):
        style, got, picked = box_style_and_count(block, fname)
        if style is None:
            err(f"{tag} 「{fname}」未识别到复选框（应 {want} 个）")
            continue
        if got != want:
            err(f"{tag} 「{fname}」复选框应 {want} 个，实际 {got} 个")
        if file_style and style != file_style:
            err(
                f"{tag} 「{fname}」写法与全文件不一致"
                f"（全文件主写法：{STYLE_NAME[file_style]}；此处：{STYLE_NAME[style]}）"
            )
        if fname == "错因" and picked:
            marked_any = True
            for p in picked:
                mark_log[p] = mark_log.get(p, 0) + 1
    if marked_any:
        mark_log["__答题数__"] = mark_log.get("__答题数__", 0) + 1


def strip_questions(seg: str) -> str:
    """去掉模块段里的错题块，只留下知识点部分。"""
    parts = re.split(r"(?m)^####\s+", seg)
    return parts[0] + "".join(p for p in parts[1:] if not p.startswith("错题"))


def check_point_ids(modules):
    total = 0
    all_ids = set()
    for idx, numeral, name, seg in modules:
        area = strip_questions(seg)
        for m in re.finditer(r"\[K(\d+)-(\d+)\]", area):
            total += 1
            kid = f"K{m.group(1)}-{m.group(2)}"
            if kid in all_ids:
                err(f"知识点编号重复: {kid}")
            all_ids.add(kid)
            if idx is not None and int(m.group(1)) != idx:
                err(f"{kid} 出现在模块{numeral}里，K 号的模块位应为 {idx}")
    return total, all_ids


def check_point_refs(questions, all_ids) -> None:
    for n, _, block in questions:
        val = block_field(block, "考点")
        if not val:
            continue
        for kid in re.findall(r"K\d+-\d+", val):
            if kid not in all_ids:
                err(f"错题 {n:02d} 的「考点」引用了未定义的知识点 {kid}")


def check_overview(body: str, modules, question_counts: dict) -> None:
    m = re.search(r"^##\s+零、模块总览与错因诊断\s*$", body, re.M)
    if not m:
        err("缺少必备章节: ## 零、模块总览与错因诊断")
        return
    seg = body[m.end():]
    nxt = re.search(r"^##\s+", seg, re.M)
    seg = seg[: nxt.start()] if nxt else seg

    rows = {}
    for line in seg.splitlines():
        mm = re.match(r"^\|\s*([一二三四五六七八九十]+)\s*\|\s*([^|]+?)\s*\|[^|]*\|\s*(\d+)\s*\|", line)
        if mm:
            idx = cn2int(mm.group(1))
            rows[idx] = (mm.group(2).strip(), int(mm.group(3)))

    if not rows:
        err("总览表为空或格式不对（应为 | 编号 | 模块 | 核心考点 | 错题数 | 高频错因 |）")
        return

    for idx, numeral, name, _ in modules:
        if idx not in rows:
            err(f"总览表缺「模块{numeral} {name}」这一行")
            continue
        want = question_counts.get(idx, 0)
        got = rows[idx][1]
        if got != want:
            err(f"总览表写模块{numeral}有 {got} 题，正文实际 {want} 题")

    extra = set(rows) - {i for i, *_ in modules}
    for e in sorted(x for x in extra if x):
        err(f"总览表有「{rows[e][0]}」，但正文没有对应模块")


def check_distribution(body: str, mark_log: dict, total_q: int) -> None:
    m = re.search(r"错因分布[^：:\n]*[：:]\s*(.+)", body)
    if not m:
        warn("未找到「错因分布」行，建议在「零」里补一行统计")
        return
    line = m.group(1)
    if "待标注" in line:
        mm = re.search(r"(\d+)\s*题", line)
        if mm and int(mm.group(1)) != total_q:
            err(f"错因分布写「待标注（{mm.group(1)} 题）」，文件里实际有 {total_q} 题")
        if mark_log.get("__答题数__"):
            err("错因分布写「待标注」，但有题目已经标了错因")
        return
    pairs = re.findall(r"([^\s·、（）()]+)\s+(\d+)", re.sub(r"[（(][^）)]*[）)]", "", line))
    if not pairs:
        return
    stated = {k: int(v) for k, v in pairs}
    marked_total = sum(v for k, v in mark_log.items() if not k.startswith("__"))
    unmarked = total_q - mark_log.get("__答题数__", 0)

    for name, num in stated.items():
        if name == "未标注":
            if unmarked != num:
                err(f"错因分布写「未标注 {num}」，实际 {unmarked} 题没有任何标注")
            continue
        actual = mark_log.get(name, 0)
        if actual != num:
            err(f"错因分布写「{name} {num}」，卡片里实际标了 {actual} 处")

    missing = [k for k in mark_log if not k.startswith("__") and k not in stated]
    if missing and marked_total:
        warn(f"这些错因在卡片里标了但没进分布统计: {'、'.join(missing[:4])}")


def check_ledger(body: str, parse_missing: int) -> None:
    m = re.search(r"^##\s+附录\s*E\s+整理留痕\s*$", body, re.M)
    if not m:
        err("缺少必备章节: ## 附录 E 整理留痕")
        return
    seg = body[m.end():]
    nxt = re.search(r"^##\s+", seg, re.M)
    seg = seg[: nxt.start()] if nxt else seg

    row = None
    for line in seg.splitlines():
        if re.match(r"^\|\s*解析缺失\s*\|", line):
            row = line
    if row is None:
        err("附录 E 里没有「解析缺失」这一行")
        return
    ids = re.findall(r"错题\s*(\d+)", row)
    if parse_missing and len(ids) != parse_missing:
        err(f"front matter `parse_missing` 是 {parse_missing}，附录 E 里列了 {len(ids)} 题")
    if parse_missing == 0 and ids:
        err(f"front matter `parse_missing` 是 0，附录 E 却列了 {len(ids)} 题解析缺失")


def print_health(module_count: int, q_count: int, modules, counts: dict) -> None:
    print("结构健康度:")
    print(
        f"  模块 {module_count} 个（提示阈值 {TH_MODULES}）"
        f" · 错题 {q_count} 道（提示阈值 {TH_TOTAL_Q}）"
    )
    if modules:
        idx, numeral, name, _ = max(modules, key=lambda m: counts.get(m[0], 0))
        print(f"  最重模块 模块{numeral} {name} {counts.get(idx, 0)} 题（提示阈值 {TH_MODULE_Q}）")

    tips = []
    for idx, numeral, name, _ in modules:
        c = counts.get(idx, 0)
        if c > TH_MODULE_Q:
            tips.append(f"模块{numeral} {name} {c} 题，建议评估拆模块")
    empty = [f"模块{num}" for idx, num, name, _ in modules if counts.get(idx, 0) == 0]
    if len(empty) >= 3:
        tips.append(f"{len(empty)} 个模块没有错题（{'、'.join(empty[:4])}），确认是否记错归模块")
    if module_count > TH_MODULES:
        tips.append(f"模块 {module_count} 个超过 {TH_MODULES}，建议评估合并")
    if q_count > TH_TOTAL_Q:
        tips.append(f"总题数 {q_count} 超过 {TH_TOTAL_Q}，建议评估整体切分")

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
    if found:
        check_header(text, fm)

    for s in ("## 目录",):
        if not re.search(rf"^{re.escape(s)}\s*$", body, re.M):
            err(f"缺少必备章节: {s}")

    modules = parse_modules(body)
    if not modules:
        err("没有识别到任何模块（标题应为 `## 模块一 名称`）")

    numbers = [m[0] for m in modules if m[0]]
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        warn(f"模块序号不连续: {[m[1] for m in modules]}")

    questions = parse_questions(body)
    file_style = detect_file_style(questions)

    crammed: list = []
    mark_log: dict = {}
    for n, title, block in questions:
        check_question(n, title, block, file_style, crammed, mark_log)
    if crammed:
        warn(f"{len(crammed)} 道题的选项挤在一行，应一项一行或改用表格")

    qnums = [q[0] for q in questions]
    if qnums:
        dup = {i for i in qnums if qnums.count(i) > 1}
        if dup:
            err(f"错题号重复: {', '.join(str(d) for d in sorted(dup))}")
        gaps = sorted(set(range(1, max(qnums) + 1)) - set(qnums))
        if gaps:
            err(f"错题号跳号，缺: {', '.join(str(g) for g in gaps)}")

    point_count, all_ids = check_point_ids(modules)
    check_point_refs(questions, all_ids)

    counts = {}
    for idx, _, _, seg in modules:
        counts[idx] = len(re.findall(r"^####\s+错题\s*\d+", seg, re.M))

    check_overview(body, modules, counts)
    check_distribution(body, mark_log, len(questions))
    if "parse_missing" in fm and re.fullmatch(r"\d+", fm["parse_missing"]):
        check_ledger(body, int(fm["parse_missing"]))

    actual = {
        "modules": len(modules),
        "questions": len(questions),
        "points": point_count,
    }
    for k, v in actual.items():
        if k in fm and re.fullmatch(r"\d+", fm[k]) and int(fm[k]) != v:
            err(f"front matter `{k}` 写的是 {fm[k]}，正文实际 {v}")

    print(f"校验对象: {path.name}")
    print(f"  模块 {len(modules)} / 错题 {len(questions)} / 知识点 {point_count}")
    if file_style:
        print(f"  复选框写法: {STYLE_NAME[file_style]}")
    if mark_log.get("__答题数__"):
        print(f"  标注进度: {mark_log['__答题数__']}/{len(questions)} 题已标错因")
    for w in warns:
        print(f"  ⚠ {w}")
    if errors:
        print(f"\n✗ 发现 {len(errors)} 个错误:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("\n✓ 结构校验通过\n")
    print_health(len(modules), len(questions), modules, counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
