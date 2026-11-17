#!/usr/bin/env python3
"""错题本结构校验器。

用法:
    python3 check_notebook.py <错题本.md>

校验项:
    1. front matter 字段齐全且为数字
    2. 模块数、错题数、知识点数与 front matter 一致
    3. Q 编号连续无跳号、无重复
    4. 每道错题的必填字段齐全
    5. 错因 7 项、掌握度 3 档的复选框数量正确
    6. 「考点」引用的知识点 ID 在正文中有定义
    7. 必备章节（目录/通用方法索引/M99/重复项/待人工确认/解析缺失/整理说明）存在
    8. 变化信息段存在

退出码: 0 通过（可含警告），1 有错误。
"""

import re
import sys
from pathlib import Path

FRONT_KEYS = ["title", "updated", "modules", "questions", "points", "parse_missing"]
NUM_KEYS = ["modules", "questions", "points", "parse_missing"]

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
    for k in NUM_KEYS:
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
    """抽取 `- 字段名：值` 形式的字段，返回 {字段名: 值}（重复取首个）"""
    d = {}
    for line in block.splitlines():
        m = re.match(r"^\s*[-*]\s*([^：:]{1,20})[：:]\s*(.*)$", line)
        if m:
            key = m.group(1).strip()
            d.setdefault(key, m.group(2).strip())
    return d


def match_field(d: dict, name: str):
    for k in d:
        if k.startswith(name):
            return d[k]
    return None


def count_checkbox_vertical(block: str, after_line_idx: int) -> int:
    """竖排变体: 数紧随其后的 - [ ] 行"""
    lines = block.splitlines()
    n = 0
    for line in lines[after_line_idx + 1:]:
        if re.match(r"^\s*-\s*\[[ xX]\]\s*\S", line):
            n += 1
        elif line.strip() == "":
            continue
        else:
            break
    return n


def check_question(qid: int, title: str, block: str) -> None:
    tag = f"Q-{qid:02d}"
    d = field_lines(block)

    if not title:
        warn(f"{tag} 标题为空，建议写一句话说清错在哪")

    for f in REQ_FIELDS:
        if match_field(d, f) is None:
            err(f"{tag} 缺少字段「{f}」")

    # 复选框
    lines = block.splitlines()
    for fname, want in (("错因", ERR_TYPE_COUNT), ("掌握度", MASTER_COUNT)):
        val = match_field(d, fname)
        if val is None:
            continue
        got = val.count("☐")
        if got == 0 and val.strip() == "":
            idx = next(
                (i for i, l in enumerate(lines)
                 if re.match(rf"^\s*[-*]\s*{fname}[：:]\s*$", l)),
                None,
            )
            if idx is not None:
                got = count_checkbox_vertical(block, idx)
        if got != want:
            err(f"{tag} 「{fname}」复选框应 {want} 个，实际 {got} 个")
        elif "☑" in val or re.search(r"\[[xX]\]", val):
            err(f"{tag} 「{fname}」存在已勾选项，生成时不应预勾")

    # 正解思路步数
    steps = re.findall(r"^\s{2,}\d+[.、)]\s+\S", block, re.M)
    if len(steps) > 5:
        warn(f"{tag} 「正解思路」{len(steps)} 步，规范是 ≤5 步")


def check_ids(body: str, questions) -> None:
    def_ids = set(
        re.findall(r"^\s*(?:[-*]\s*)?(\[K-[A-Z0-9]+-\d+\])", body, re.M)
    )
    ref_ids = set()
    for _, _, block in questions:
        m = re.search(r"^\s*[-*]\s*考点[：:]\s*(.*)$", block, re.M)
        if m:
            ref_ids.update(re.findall(r"\[K-[A-Z0-9]+-\d+\]", m.group(1)))
    missing = sorted(ref_ids - def_ids)
    for mid in missing:
        err(f"「考点」引用了未定义的知识点 {mid}")
    unused = sorted(def_ids - ref_ids)
    if unused:
        warn(f"有 {len(unused)} 条知识点没有错题引用（若非孤儿知识点请检查）: {', '.join(unused[:5])}")
    return len(def_ids)


def count_parse_missing(body: str) -> int:
    sec = get_section(body, "## 解析缺失清单")
    if not sec or "本期无" in sec:
        return 0
    return len(re.findall(r"^\|\s*Q-\d+", sec, re.M))


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
    for qid, title, block in questions:
        check_question(qid, title, block)

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

    module_count = len(
        re.findall(r"^##\s+M(?!99\b)\d+\s+\S", body, re.M)
    )
    pm = count_parse_missing(body)

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
    name = path.name
    print(f"校验对象: {name}")
    print(f"  模块 {module_count} / 错题 {len(questions)} / 知识点 {point_count} / 解析缺失 {pm}")
    for w in warns:
        print(f"  ⚠ {w}")
    if errors:
        print(f"\n✗ 发现 {len(errors)} 个错误:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("\n✓ 结构校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
