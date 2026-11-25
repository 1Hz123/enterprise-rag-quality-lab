# ruff: noqa: E501
"""Build the beginner-oriented Word guide for Enterprise RAG Quality Lab."""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "企业RAG质量实验室-新手项目讲解.docx"

NAVY = "12324A"
BLUE = "176B87"
LIGHT_BLUE = "EAF3F8"
PALE_BLUE = "F4F8FB"
GREEN = "EAF5EF"
AMBER = "FFF6E5"
GREY = "F3F5F7"
MID_GREY = "5B6770"
WHITE = "FFFFFF"
DARK = "1E2A32"


def set_run_font(
    run,
    *,
    name: str = "Microsoft YaHei",
    size: float = 10.5,
    bold: bool = False,
    color: str | None = None,
    italic: bool = False,
) -> None:
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    if color:
        run.font.color.rgb = RGBColor.from_string(color)
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), name)
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)


def shade_cell(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top: int = 90, bottom: int = 90, left: int = 120, right: int = 120) -> None:
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for margin, value in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        node = tc_mar.find(qn(f"w:{margin}"))
        if node is None:
            node = OxmlElement(f"w:{margin}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_cell_text(
    cell,
    text: str,
    *,
    bold: bool = False,
    color: str = DARK,
    size: float = 9,
    align: WD_ALIGN_PARAGRAPH = WD_ALIGN_PARAGRAPH.LEFT,
) -> None:
    cell.text = ""
    p = cell.paragraphs[0]
    p.alignment = align
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = 1.15
    run = p.add_run(text)
    set_run_font(run, size=size, bold=bold, color=color)
    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER


def style_document(document: Document) -> None:
    styles = document.styles
    normal = styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = RGBColor.from_string(DARK)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.paragraph_format.line_spacing = 1.35
    normal.paragraph_format.space_after = Pt(5)

    for name, size, color, before, after in (
        ("Heading 1", 18, NAVY, 0, 12),
        ("Heading 2", 13.5, BLUE, 15, 6),
        ("Heading 3", 11.5, NAVY, 10, 4),
    ):
        style = styles[name]
        style.font.name = "Microsoft YaHei"
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor.from_string(color)
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True

    for name in ("List Bullet", "List Number"):
        style = styles[name]
        style.font.name = "Microsoft YaHei"
        style.font.size = Pt(10.5)
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.paragraph_format.line_spacing = 1.3
        style.paragraph_format.space_after = Pt(3)


def add_page_number_footer(section, text: str) -> None:
    footer = section.footer
    paragraph = footer.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run(text)
    set_run_font(run, size=8.5, color=MID_GREY)
    run = paragraph.add_run("  ·  ")
    set_run_font(run, size=8.5, color=MID_GREY)

    field_begin = OxmlElement("w:fldChar")
    field_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    field_separate = OxmlElement("w:fldChar")
    field_separate.set(qn("w:fldCharType"), "separate")
    field_text = OxmlElement("w:t")
    field_text.text = "1"
    field_end = OxmlElement("w:fldChar")
    field_end.set(qn("w:fldCharType"), "end")
    run._r.append(field_begin)
    run._r.append(instr)
    run._r.append(field_separate)
    run._r.append(field_text)
    run._r.append(field_end)
    set_run_font(run, size=8.5, color=MID_GREY)


def add_header(section) -> None:
    header = section.header
    paragraph = header.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = paragraph.add_run("Enterprise RAG Quality Lab  ·  新手项目讲解")
    set_run_font(run, size=8.5, color=MID_GREY)


def add_paragraph(
    document: Document,
    text: str,
    *,
    size: float = 10.5,
    bold: bool = False,
    color: str = DARK,
    align: WD_ALIGN_PARAGRAPH = WD_ALIGN_PARAGRAPH.JUSTIFY,
    space_after: float = 5,
    indent: bool = False,
) -> None:
    p = document.add_paragraph()
    p.alignment = align
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = 1.35
    if indent:
        p.paragraph_format.first_line_indent = Cm(0.74)
    run = p.add_run(text)
    set_run_font(run, size=size, bold=bold, color=color)


def add_h1(document: Document, text: str, *, page_break: bool = True) -> None:
    if page_break:
        document.add_page_break()
    p = document.add_heading(text, level=1)
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(10)
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "10")
    bottom.set(qn("w:space"), "3")
    bottom.set(qn("w:color"), BLUE)
    pBdr.append(bottom)
    pPr.append(pBdr)


def add_h2(document: Document, text: str) -> None:
    document.add_heading(text, level=2)


def add_h3(document: Document, text: str) -> None:
    document.add_heading(text, level=3)


def add_bullets(document: Document, items: list[str]) -> None:
    for item in items:
        p = document.add_paragraph(style="List Bullet")
        run = p.add_run(item)
        set_run_font(run, size=10.5)
        p.paragraph_format.space_after = Pt(3)


def add_numbered(document: Document, items: list[str]) -> None:
    for item in items:
        p = document.add_paragraph(style="List Number")
        run = p.add_run(item)
        set_run_font(run, size=10.5)
        p.paragraph_format.space_after = Pt(3)


def add_table(
    document: Document,
    headers: list[str],
    rows: list[list[str]],
    *,
    widths: list[float] | None = None,
    font_size: float = 9,
) -> None:
    table = document.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    table.autofit = False
    if widths:
        for index, width in enumerate(widths):
            table.columns[index].width = Cm(width)
    header_cells = table.rows[0].cells
    for index, header in enumerate(headers):
        shade_cell(header_cells[index], NAVY)
        set_cell_text(header_cells[index], header, bold=True, color=WHITE, size=font_size)
        if widths:
            header_cells[index].width = Cm(widths[index])
    for row_index, row in enumerate(rows):
        cells = table.add_row().cells
        for index, value in enumerate(row):
            if row_index % 2 == 1:
                shade_cell(cells[index], PALE_BLUE)
            set_cell_text(cells[index], value, size=font_size)
            if widths:
                cells[index].width = Cm(widths[index])
    # Repeat header when a table spans pages.
    trPr = table.rows[0]._tr.get_or_add_trPr()
    tblHeader = OxmlElement("w:tblHeader")
    tblHeader.set(qn("w:val"), "true")
    trPr.append(tblHeader)
    document.add_paragraph().paragraph_format.space_after = Pt(0)


def add_callout(
    document: Document,
    title: str,
    body: str,
    *,
    fill: str = LIGHT_BLUE,
    title_color: str = NAVY,
) -> None:
    table = document.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    table.columns[0].width = Cm(16.5)
    cell = table.rows[0].cells[0]
    cell.width = Cm(16.5)
    shade_cell(cell, fill)
    set_cell_margins(cell, top=130, bottom=130, left=180, right=180)
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run(title)
    set_run_font(run, size=9.5, bold=True, color=title_color)
    p2 = cell.add_paragraph()
    p2.paragraph_format.space_after = Pt(0)
    p2.paragraph_format.line_spacing = 1.25
    run = p2.add_run(body)
    set_run_font(run, size=9.5, color=DARK)
    document.add_paragraph().paragraph_format.space_after = Pt(0)


def add_code(document: Document, code: str, *, caption: str | None = None) -> None:
    if caption:
        p = document.add_paragraph()
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after = Pt(2)
        run = p.add_run(caption)
        set_run_font(run, size=8.5, color=MID_GREY, italic=True)

    table = document.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    table.columns[0].width = Cm(16.5)
    cell = table.rows[0].cells[0]
    cell.width = Cm(16.5)
    shade_cell(cell, GREY)
    set_cell_margins(cell, top=120, bottom=120, left=160, right=160)
    cell.text = ""
    first = True
    for line in code.rstrip("\n").splitlines():
        p = cell.paragraphs[0] if first else cell.add_paragraph()
        first = False
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = 1.0
        run = p.add_run(line if line else " ")
        set_run_font(run, name="Consolas", size=8.2, color=DARK)
    document.add_paragraph().paragraph_format.space_after = Pt(0)


def add_quote(document: Document, text: str, *, source: str | None = None) -> None:
    p = document.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.6)
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(4)
    run = p.add_run(f"“{text}”")
    set_run_font(run, size=10, italic=True, color=BLUE)
    if source:
        run = p.add_run(f"  —— {source}")
        set_run_font(run, size=8.5, color=MID_GREY)


def add_cover(document: Document) -> None:
    for _ in range(3):
        document.add_paragraph()

    banner = document.add_table(rows=1, cols=1)
    banner.alignment = WD_TABLE_ALIGNMENT.CENTER
    banner.autofit = False
    banner.columns[0].width = Cm(16.5)
    cell = banner.rows[0].cells[0]
    cell.width = Cm(16.5)
    shade_cell(cell, NAVY)
    set_cell_margins(cell, top=360, bottom=360, left=360, right=360)
    cell.text = ""
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_after = Pt(12)
    run = p.add_run("从零读懂")
    set_run_font(run, size=20, bold=True, color="BFE3F2")

    p = cell.add_paragraph()
    p.paragraph_format.space_after = Pt(10)
    run = p.add_run("Enterprise RAG\nQuality Lab")
    set_run_font(run, size=31, bold=True, color=WHITE)

    p = cell.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    run = p.add_run("企业制度问答 · RAG 质量评测 · 智能组织运行时")
    set_run_font(run, size=12, color="D8ECF5")

    p = cell.add_paragraph()
    p.paragraph_format.space_after = Pt(0)
    run = p.add_run("写给完全新手的项目讲解书")
    set_run_font(run, size=11, bold=True, color="F4C77A")

    document.add_paragraph()
    p = document.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("版本 1.1.0  ·  2026-11-18  ·  以当前仓库代码为准")
    set_run_font(run, size=9.5, color=MID_GREY)

    p = document.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("读完你能：讲清 RAG、跑起项目、跟完一次请求、改一个功能、说清边界")
    set_run_font(run, size=10, color=BLUE, bold=True)

    document.add_paragraph()
    add_callout(
        document,
        "先记住一句话",
        "这个项目不是“会聊天的 Demo”，而是一个能回答、能引用、能拒答、能评测、能追溯版本的企业制度问答质量实验室。",
        fill=AMBER,
        title_color="8A5A00",
    )


def add_front_matter(document: Document) -> None:
    add_h1(document, "开始之前：5 分钟抓住这个项目", page_break=True)
    add_paragraph(
        document,
        "如果你第一次接触 RAG，先不要急着看代码。你只需要先回答三个问题："
        "系统从哪里找资料？答案凭什么可信？改坏了怎么发现？这个项目就是围绕这三件事搭起来的。",
    )

    add_h2(document, "它到底解决什么问题")
    add_table(
        document,
        ["问题", "没有这套系统时", "这个项目怎么做"],
        [
            ["答得对不对", "模型可能凭记忆编造", "先检索企业文档，再基于证据回答"],
            ["引用是否可信", "只在文末放几个链接", "每个事实句带引用编号，并校验来源、日期和句子 quote"],
            ["制度是否为当前版本", "旧版和新版混在一起", "用 policy_key + effective_from/until + as_of 过滤"],
            ["答不出来怎么办", "硬猜", "低分、无证据、注入攻击时明确拒答"],
            ["改完有没有变差", "凭感觉", "21 题固定评测 + 质量门禁 + 基线回归"],
            ["出错能不能追", "只有最终答案", "保存 run、候选块、引用、智能体轨迹和质量检查"],
        ],
        widths=[3.2, 5.2, 8.1],
    )

    add_h2(document, "关键数字（当前 v1.1.0）")
    add_table(
        document,
        ["指标", "当前值", "怎么理解"],
        [
            ["示例语料", "7 篇", "模拟企业制度文档，包含 2025/2026 两版员工手册"],
            ["评测题", "21 题", "19 道回答、1 道拒答、1 道注入抵抗"],
            ["测试", "53 项", "API、检索、版本、摄入、审计、智能体、注入"],
            ["覆盖率", "82.51%", "pytest-cov 统计，CI 要求 ≥ 80%"],
            ["基线", "21/21", "固定语料上的回归基线，不等于通用能力"],
            ["非法引用率", "0.0%", "raw 输出中没有越界引用，门禁 ≤ 2%"],
            ["延迟", "p50 7.5 ms / p95 7.6 ms", "本机进程内计算，不是生产 SLA"],
            ["安全扫描", "0 CVE / 0 Bandit", "依赖与源码扫描；不代表生产安全"],
        ],
        widths=[3.4, 4.9, 8.2],
    )

    add_h2(document, "怎么读这份文档")
    add_table(
        document,
        ["阶段", "读什么", "目标"],
        [
            ["0–30 分钟", "第 1–2 章", "知道 RAG 是什么、项目为什么存在"],
            ["30–60 分钟", "第 3 章 + 第 9 章", "看懂架构，把项目跑起来"],
            ["1–2 小时", "第 4–6 章", "能跟着一次请求读代码"],
            ["2–3 小时", "第 7–8 章", "理解数据模型、评测和质量门禁"],
            ["动手", "第 10 章练习", "改文档、改配置、加评测题、故意制造失败"],
            ["面试前", "第 11–12 章 + 术语表", "能讲清取舍、指标和边界"],
        ],
        widths=[3.0, 5.6, 7.9],
    )

    add_h2(document, "章节地图")
    add_table(
        document,
        ["章节", "你会得到什么"],
        [
            ["1. 项目是什么", "建立正确预期：质量实验室，不是通用聊天机器人"],
            ["2. RAG 入门", "用开卷考试理解检索、生成、引用"],
            ["3. 架构", "六层结构、启动顺序、两条请求路径"],
            ["4. 代码地图", "每个目录/文件的职责和推荐阅读顺序"],
            ["5. 入库", "文档如何变成带来源、版本、标题路径的 chunk"],
            ["6. 问答生命周期", "七道关卡、检索分数、raw 引用审计、多时点、经验"],
            ["7. 数据模型", "七张表、fingerprint、版本快照和边界"],
            ["8. 评测", "21 题、指标、质量门禁和 baseline-2%"],
            ["9. 跑起来", "uv / pip / Docker、API、配置、常见故障"],
            ["10. 动手", "六个从易到难的练习"],
            ["11. 工程与安全", "测试、CI、容器、安全扫描和不能吹的地方"],
            ["12. 面试表达", "60 秒介绍、深挖问题、下一步路线"],
        ],
        widths=[4.4, 12.1],
        font_size=8.8,
    )

    add_callout(
        document,
        "阅读建议",
        "不要从 agents 目录开始读。先读 app/rag/pipeline.py，把“检索 → 生成 → 引用校验”这条主线走通；"
        "再看 agents，否则很容易被角色名和编排逻辑带偏。",
    )


def add_chapter_1(document: Document) -> None:
    add_h1(document, "第 1 章  这个项目是什么，不是什么")
    add_paragraph(
        document,
        "Enterprise RAG Quality Lab 是一个离线可运行的企业制度问答实验项目。"
        "它把“文档入库、混合检索、受控生成、引用审计、版本时间旅行、经验复用、固定评测”放进同一条可重复运行的链路。",
    )

    add_h2(document, "1.1 一个生活化类比")
    add_paragraph(
        document,
        "把大模型想象成一个记忆力很好、但容易把不同公司制度记混的实习生。RAG 的做法不是让他背下所有制度，"
        "而是每次回答前先给他一叠从公司档案室取出的资料卡：先查资料，再写答案，最后注明“这句话来自哪张卡”。",
    )
    add_bullets(
        document,
        [
            "档案室 = 企业文档库（本项目用 SQLite + chunks 表）。",
            "资料卡 = chunk（切块）。",
            "按主题找卡 = 检索（向量 + BM25）。",
            "实习生写答案 = 生成（extractive 或 OpenAI 兼容模型）。",
            "主管检查引用 = Auditor。",
            "每月考试 = 固定评测集与质量门禁。",
        ],
    )

    add_h2(document, "1.2 三个核心追问")
    add_numbered(
        document,
        [
            "答案有没有依据？——每个事实句必须有引用，引用必须能回到真实 chunk。",
            "依据是不是当前有效版本？——制度有生效窗口，问题可以带 as_of 查历史时点。",
            "系统有没有变差？——用固定题集、固定 as_of、固定指标做回归，而不是凭感觉。",
        ],
    )

    add_h2(document, "1.3 它不是什么")
    add_table(
        document,
        ["它不是", "原因"],
        [
            ["通用聊天机器人", "默认只回答知识库内的事实；资料不足会拒答"],
            ["生产级多租户知识库", "没有用户认证、租户隔离、文档 ACL、限流和 PII 脱敏"],
            ["SOTA 检索系统", "默认是 384 维哈希向量 + BM25，追求离线、确定性和可解释"],
            ["大模型能力证明", "默认生成器是抽取式的，21/21 是容易集上的回归结果"],
            ["完整版本化检索", "有版本快照 API，但旧 as_of 的旧 chunk 检索仍未实现"],
        ],
        widths=[4.2, 12.3],
    )
    add_callout(
        document,
        "面试时最容易被追问的点",
        "不要说“我的 RAG 准确率 100%”。要说“在 7 篇示例文档、21 道固定题、固定 as_of 和抽取式生成器下，"
        "回归基线是 21/21；它证明的是管线可重复和门禁有效，不是通用准确率”。",
        fill=AMBER,
        title_color="8A5A00",
    )


def add_chapter_2(document: Document) -> None:
    add_h1(document, "第 2 章  10 分钟看懂 RAG")
    add_paragraph(
        document,
        "RAG（Retrieval-Augmented Generation，检索增强生成）可以先理解成“开卷考试”："
        "模型不允许凭记忆答题，必须先翻资料，再根据资料写答案。",
    )

    add_h2(document, "2.1 为什么需要 RAG")
    add_table(
        document,
        ["纯大模型的问题", "RAG 的应对"],
        [
            ["知识过期", "每次从最新文档检索"],
            ["企业私有知识不在训练集", "把企业文档放进知识库"],
            ["容易编造", "要求每个事实句引用证据，资料不足就拒答"],
            ["无法追溯", "保存候选块、引用、版本、分数和运行轨迹"],
            ["改制度后答案没变", "按生效日期过滤版本"],
        ],
        widths=[6.5, 10.0],
    )

    add_h2(document, "2.2 一条最小 RAG 链路")
    add_table(
        document,
        ["步骤", "输入", "输出", "本项目对应"],
        [
            ["加载", "md/txt 文件", "LoadedDocument", "knowledge/loader.py"],
            ["切块", "长文档", "带元数据的 Chunk", "knowledge/splitter.py"],
            ["向量化", "chunk 文本", "384 维向量", "knowledge/embeddings.py"],
            ["检索", "问题 + as_of", "Top-K chunk", "knowledge/retriever.py"],
            ["生成", "问题 + 证据", "带引用编号的答案", "rag/generator.py"],
            ["校验", "答案 + 引用", "清洗后答案 + 审计结果", "rag/pipeline.py + auditor.py"],
        ],
        widths=[2.2, 4.1, 5.0, 5.2],
    )

    add_h2(document, "2.3 用一个真实问题走一遍")
    add_paragraph(
        document,
        "问题：员工入职满三年，年假有多少天？  \n"
        "当 as_of=2025-06-01 时，应命中 2025 版员工手册，答案是 8 天；"
        "当 as_of=2026-06-01 时，应命中 2026 版，答案是 10 天。",
    )
    add_numbered(
        document,
        [
            "规范化问题，并检查是否包含“忽略之前指令”等注入模式。",
            "按 as_of 过滤掉不在有效期内的文档。",
            "把问题向量和每个 chunk 向量做余弦相似度，同时算 BM25 关键词分。",
            "融合分数排序，取 top_k=5。",
            "把候选块清洗、编号，交给生成器。",
            "生成器只从证据句中选择内容，并在句末加 [编号]。",
            "pipeline 删除不存在的编号，同时记录原始非法引用数量。",
            "Auditor 检查引用、日期、任务覆盖、quote 和安全标记。",
            "通过后保存 run；高质量答案还可进入经验库。",
        ],
    )
    add_callout(
        document,
        "关键区别：检索不是“搜索框”",
        "检索结果会直接决定生成器能看到什么。检索错了，后面写得再漂亮也只是“有引用地答错”。"
        "所以本项目把 hit@k、MRR、引用准确率、非法引用率分开测。",
    )


def add_chapter_3(document: Document) -> None:
    add_h1(document, "第 3 章  项目架构：六层一条线")
    add_paragraph(
        document,
        "这个项目可以按“请求从外到内”的六层来理解。每一层只做一件事，层与层之间用 Pydantic 模型和 dataclass 传递。",
    )

    add_h2(document, "3.1 六层结构")
    add_table(
        document,
        ["层", "职责", "典型文件"],
        [
            ["API 层", "接收 HTTP 请求、参数校验、把异常映射成 400/404/409", "app/api/*.py"],
            ["服务层", "组织一次完整用例：聊天、文档 CRUD、评测", "app/services/*.py"],
            ["智能组织层", "Coordinator/Retriever/Reasoner/Auditor/Experience 编排", "app/agents/*.py"],
            ["RAG 层", "检索、提示词、生成、引用校验、拒答", "app/rag/*.py"],
            ["知识层", "加载、切块、向量化、混合检索", "app/knowledge/*.py"],
            ["数据层", "SQLite schema、迁移、连接管理", "app/db.py"],
        ],
        widths=[3.2, 9.2, 4.1],
    )

    add_h2(document, "3.2 启动时发生了什么")
    add_numbered(
        document,
        [
            "create_app() 读取 Settings，解析数据库、语料、评测集路径。",
            "FastAPI lifespan 创建 SQLite 并执行 schema/migration。",
            "构建 HashingEmbeddings、Retriever、Generator、RAGPipeline。",
            "构建 ExperienceService、IntelligentOrganization、DocumentService、ChatService、Evaluator、EvolutionAgent。",
            "回填旧文档 fingerprint；如果 auto_ingest_sample_docs=true，幂等导入 data/sample_docs。",
            "请求到来时，由 ChatService 调用 IntelligentOrganization，或由 Evaluator 直接调用 RAGPipeline。",
        ],
    )
    add_code(
        document,
        """# app/main.py（简化）
database.initialize()
embeddings = build_embeddings(...)
retriever = Retriever(database, embeddings)
generator = build_generator(settings)
pipeline = RAGPipeline(retriever, generator, settings)
experience = ExperienceService(database)
organization = IntelligentOrganization(pipeline, experience, settings)""",
        caption="启动装配的核心对象",
    )

    add_h2(document, "3.3 两条请求路径")
    add_table(
        document,
        ["路径", "入口", "特点"],
        [
            ["直接 RAG", "RAGPipeline.answer()", "检索一次、生成一次，评测和调试最常用"],
            ["智能组织", "IntelligentOrganization.answer()", "先查经验，再拆任务、多智能体协作、审计、沉淀"],
        ],
        widths=[3.5, 6.0, 7.0],
    )
    add_callout(
        document,
        "读代码顺序",
        "main.py → rag/pipeline.py → knowledge/retriever.py → agents/orchestrator.py → services/chat_service.py → evaluation/evaluator.py。"
        "这条线走通，项目就通了 70%。",
    )


def add_chapter_4(document: Document) -> None:
    add_h1(document, "第 4 章  代码地图：每个文件负责什么")
    add_paragraph(
        document,
        "不要试图一次读完所有文件。下面按“从入口到细节”的顺序给出阅读地图。"
        "每一行都告诉你：这个文件解决什么问题，读它时重点看什么。",
    )

    add_h2(document, "4.1 目录总览")
    add_code(
        document,
        """app/
├── main.py                  # FastAPI 工厂、lifespan、对象装配
├── config.py                # 环境变量、路径、默认参数、校验
├── schemas.py               # API 输入输出模型
├── db.py                    # SQLite schema、迁移、连接
├── api/                     # 路由：chat/documents/runs/eval/organization/health
├── services/                # 聊天与文档用例编排
├── agents/                  # 协调、检索、推理、审计、经验、进化
├── rag/                     # pipeline、prompt、generator、query
├── knowledge/               # loader、splitter、embeddings、retriever
└── evaluation/              # dataset、evaluator、report

data/sample_docs/            # 7 篇示例企业文档
eval/dataset.jsonl           # 21 道固定评测题
eval/results/baseline.json   # 提交的回归基线
tests/                       # 53 项测试
docs/                        # 架构、实验日志、审计、本文档
scripts/                     # 摄入、构建脚本
""",
        caption="项目目录（节选）",
    )

    add_h2(document, "4.2 推荐阅读顺序")
    add_table(
        document,
        ["顺序", "文件", "重点看什么"],
        [
            ["1", "app/main.py", "create_app、lifespan、对象装配、seed"],
            ["2", "app/config.py", "所有 RAG_LAB_ 环境变量和默认值"],
            ["3", "app/schemas.py", "API 契约、DocumentCreate 校验、ChatRequest"],
            ["4", "app/db.py", "7 张表、schema v4、迁移、WAL"],
            ["5", "knowledge/loader.py", "frontmatter、GBK、日期、目录导入"],
            ["6", "knowledge/splitter.py", "section、heading_path、overlap、metadata"],
            ["7", "knowledge/embeddings.py", "hashing、tokenize、cosine"],
            ["8", "knowledge/retriever.py", "BM25 + 向量 + as_of 过滤"],
            ["9", "rag/pipeline.py", "拒答、引用校验、raw 审计"],
            ["10", "agents/orchestrator.py", "经验复用、任务拆分、审计、沉淀"],
            ["11", "evaluation/evaluator.py", "逐题评分、聚合、质量门禁"],
            ["12", "tests/", "把行为固定下来，防止改坏"],
        ],
        widths=[1.2, 5.4, 9.9],
    )

    add_h2(document, "4.3 三个最重要的数据对象")
    add_h3(document, "LoadedDocument：文档进入系统时的样子")
    add_code(
        document,
        """@dataclass(frozen=True, slots=True)
class LoadedDocument:
    title: str
    source: str
    content: str
    content_type: str = "text/markdown"
    effective_from: date | None = None
    effective_until: date | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def checksum(self) -> str:
        ...  # 正文 SHA-256""",
        caption="app/knowledge/loader.py",
    )
    add_paragraph(
        document,
        "LoadedDocument 把“正文”和“身份”放在一起：标题、来源、生效日期、frontmatter 元数据。"
        "它是 document → chunk 元数据契约的起点。",
    )

    add_h3(document, "Chunk：真正进入索引的最小单位")
    add_code(
        document,
        """@dataclass(frozen=True, slots=True)
class Chunk:
    text: str
    ordinal: int
    section: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)""",
        caption="app/knowledge/splitter.py",
    )
    add_paragraph(
        document,
        "chunk 的 metadata 不是装饰品。v1.1.0 起，每个 chunk 会继承 source、checksum、policy_key、version、"
        "effective_from/until、relative_path、heading_path、embedding_model/dimension。没有这些，引用、版本审计和维度升级都做不了。",
    )

    add_h3(document, "RagResult：一次回答的完整结果")
    add_table(
        document,
        ["字段", "含义"],
        [
            ["answer", "清洗后、可返回给用户的答案"],
            ["raw_answer", "生成器原始输出，用于非法引用审计"],
            ["refused / security_blocked", "是否拒答、是否安全拦截"],
            ["citations", "引用列表：来源、版本、日期、quote、分数"],
            ["retrieved", "候选块、向量分、词面分、融合分"],
            ["metrics", "检索分数、覆盖率、非法引用数、fallback 等"],
            ["latency_ms / prompt_version / mode", "性能、提示词和生成模式"],
        ],
        widths=[5.0, 11.5],
    )


def add_chapter_5(document: Document) -> None:
    add_h1(document, "第 5 章  文档是怎么变成可检索的 chunk 的")
    add_paragraph(
        document,
        "这一章讲“入库”。如果你只记一件事：不要只传一段裸文本，要传“文本 + 来源 + 版本 + 标题路径”。"
        "RAG 的很多质量问题，根源都在入库时把元数据丢了。",
    )

    add_h2(document, "5.1 入库七步")
    add_table(
        document,
        ["步骤", "做什么", "关键文件"],
        [
            ["1. 加载", "读 md/txt，UTF-8 失败后 GB18030 回退", "loader.py"],
            ["2. 规范化", "BOM、CRLF、空字节、尾随空格", "loader.py"],
            ["3. frontmatter", "解析 title/policy_key/version/生效日期", "loader.py"],
            ["4. 判重", "用 fingerprint 判断是否同一份制度身份", "document_service.py"],
            ["5. 切块", "按标题 section、段落、句号、overlap 切分", "splitter.py"],
            ["6. 向量化", "title + section + chunk text 做 hashing embedding", "embeddings.py"],
            ["7. 落库", "documents、chunks、document_versions 三处写入", "document_service.py + db.py"],
        ],
        widths=[2.6, 9.4, 4.5],
    )

    add_h2(document, "5.2 frontmatter：制度的身份证")
    add_code(
        document,
        """---
policy_key: employee-handbook
version: "2026"
effective_from: 2026-01-01
# effective_until: 2026-12-31
title: 星河科技员工手册（2026 年版）
---""",
        caption="data/sample_docs/employee-handbook.md",
    )
    add_bullets(
        document,
        [
            "policy_key 标识同一制度族，例如 employee-handbook。",
            "version 是版本标签，例如 2025 / 2026。",
            "effective_from / effective_until 是闭区间；不填 until 表示持续有效。",
            "同一 policy_key 的生效窗口重叠，创建时返回 409。",
            "日期在 loader 中提升为 date，并统一写成 ISO YYYY-MM-DD，所以字符串排序也是时间顺序。",
        ],
    )

    add_h2(document, "5.3 为什么要切块")
    add_paragraph(
        document,
        "整篇文档太长，无法全部塞进上下文；切块后每块更聚焦，检索也更精确。但切块会带来两个新问题："
        "块与块之间断链，以及块丢失来源。项目分别用 overlap 和 metadata 来解决。",
    )
    add_table(
        document,
        ["参数", "默认值", "作用"],
        [
            ["chunk_size", "760 字符", "单块字符预算"],
            ["chunk_overlap", "110 字符", "相邻块重叠，减少边界断链；不得超过 chunk_size/2"],
            ["heading_path", "自动生成", "例如 员工手册 > 休假 > 年假"],
            ["section", "最近标题", "兼容旧字段，方便展示"],
        ],
        widths=[3.6, 3.8, 9.1],
    )
    add_callout(
        document,
        "一个容易忽略的细节",
        "标题层级不是“最近一个标题”这么简单。项目用栈维护 heading_path，才能知道“年假”属于“休假”，"
        "而“休假”属于“员工手册”。这比只存 section 更适合做引用展示和调试。",
    )

    add_h2(document, "5.4 checksum、fingerprint、版本快照")
    add_table(
        document,
        ["概念", "回答的问题", "本项目实现"],
        [
            ["checksum", "正文有没有变？", "正文 SHA-256"],
            ["fingerprint", "是不是同一份制度身份？", "正文 + 生效窗口 + 版本元数据 + 非易变 metadata"],
            ["document_versions", "历史上长什么样？", "创建/改版时保存完整正文快照"],
        ],
        widths=[3.2, 5.8, 7.5],
    )
    add_paragraph(
        document,
        "checksum 只看正文，所以两版制度正文完全一样但生效期不同，checksum 相同；"
        "fingerprint 把生效窗口和版本信息算进去，因此它们可以共存。"
        "这正是“同正文、不同生效期允许共存；同正文、同窗口仍然判重”的实现基础。",
    )
    add_paragraph(
        document,
        "注意边界：版本快照能审计历史正文，但当前检索仍然使用 documents 的当前 chunks。"
        "旧 as_of 命中旧 chunk 的完整版本化检索，是下一阶段工作。",
    )

    add_h2(document, "5.5 为什么默认哈希向量")
    add_paragraph(
        document,
        "项目默认使用 384 维 signed feature hashing：中文单字 + bigram，拉丁词，BLAKE2b 映射，L2 归一化。"
        "它的优点是完全离线、确定、无需下载模型；缺点是对同义改写和领域语义不如训练好的 embedding。",
    )
    add_bullets(
        document,
        [
            "适合：可重复实验、CI、零外部依赖的教学/评测。",
            "不适合：直接把它当成生产语义检索的上限。",
            "替换点：knowledge/embeddings.py 里的 EmbeddingModel 协议。",
        ],
    )


def add_chapter_6(document: Document) -> None:
    add_h1(document, "第 6 章  一次问答的完整生命周期")
    add_paragraph(
        document,
        "这一章跟一次真实请求。你可以在读完第 9 章后，用 Swagger 或 curl 对着自己的服务复现。"
    )

    add_h2(document, "6.1 智能组织入口")
    add_code(
        document,
        """# app/agents/orchestrator.py（简化）
question = self.pipeline.prepare_question(question)
if looks_like_prompt_injection(question):
    return blocked_result

if experience_reuse_enabled:
    cached = self._try_experience(question, as_of)
    if cached:
        return cached

plan = coordinator.plan(question, as_of)
bundles = retriever.retrieve(plan)
result, sub_answers = reasoner.answer(plan, bundles)
audit = auditor.audit(plan, result, sub_answers)
if audit.passed and ...:
    experience.save_experience(...)
return result""",
        caption="智能组织主流程",
    )

    add_h2(document, "6.2 七道关卡")
    add_table(
        document,
        ["关卡", "做什么", "失败时"],
        [
            ["1. 输入规范化", "去空白、中文数字转阿拉伯数字、长度校验", "400"],
            ["2. 直接注入检查", "命中忽略指令/套取提示词/越狱模式", "安全拒答"],
            ["3. 经验复用", "同问题、引用窗口覆盖、签名和 context hash 一致", "没命中就继续检索"],
            ["4. 检索", "向量 + BM25 + as_of 过滤 + top_k", "无结果/低分则拒答"],
            ["5. 上下文清洗", "HTML 注释、特殊 token、含注入句删除", "全被清洗则安全拒答"],
            ["6. 生成", "抽取式或 OpenAI 兼容；失败重试后回退抽取式", "生成错误/空答案"],
            ["7. 引用审计", "编号、来源、日期、quote、跨任务覆盖、安全", "不通过则不沉淀经验"],
        ],
        widths=[4.0, 8.0, 4.5],
    )

    add_h2(document, "6.3 检索分数怎么来")
    add_paragraph(
        document,
        "检索不是单一向量分。每个 chunk 的融合分数是：",
    )
    add_code(
        document,
        """score = 0.55 * vector_cosine
      + 0.40 * lexical_norm      # BM25 / (BM25 + 3)
      + 0.05 * title_coverage""",
        caption="app/knowledge/retriever.py",
    )
    add_paragraph(
        document,
        "向量分负责“语义相近”，BM25 负责“关键词命中”，标题覆盖负责“问的是不是这一篇”。"
        "混合检索比单独一路更稳，尤其适合制度名、金额、天数这类关键词很重要的企业问答。",
    )

    add_h2(document, "6.4 引用校验与 raw 审计")
    add_paragraph(
        document,
        "生成器可能输出不存在的编号，例如 [99]。如果直接把它从答案里删掉再评测，指标会看起来很好，"
        "但这是在“洗体温计”。v1.1.0 的做法是：",
    )
    add_numbered(
        document,
        [
            "保留 raw_answer。",
            "统计原始标记总数和越界标记数。",
            "从 answer 中删除非法标记，保证用户看不到假引用。",
            "把 invalid_citation_count/rate 写进 metrics。",
            "评测门禁要求非法引用率 ≤ 2%；测试里故意输出 [99] 必须让门禁失败。",
        ],
    )
    add_callout(
        document,
        "为什么这条很重要",
        "一个“看起来 100%”的评测，可能只是把错误清洗掉了。raw 审计让清洗前后的差异可见，"
        "这是评测项目最起码的诚实性。",
        fill=GREEN,
        title_color="1E6B45",
    )

    add_h2(document, "6.5 多时点对比")
    add_paragraph(
        document,
        "问题里出现两个以上年份且带“区别/对比/变化/新旧”等词时，Coordinator 会拆成多个子任务。"
        "历史年份取该年最后一天，当前年取请求日期，最多 5 个时点。",
    )
    add_code(
        document,
        """2025 年 vs 2026 年：
  year-2025 -> as_of 2025-12-31
  year-2026 -> as_of 请求日期（若为 2026 年）

每个子任务独立检索、生成、审计；
Reasoner 合并引用编号，输出按单位比较的数值 diff。""",
        caption="多时点拆分逻辑",
    )
    add_paragraph(
        document,
        "结构化 diff 目前做到“按单位比较数值变化/未变”，例如 8 天 → 10 天；"
        "还没有做到完整的主体、适用条件、数值、单位四元组对齐。这是已知边界。",
    )

    add_h2(document, "6.6 经验库：组织记忆")
    add_paragraph(
        document,
        "审计通过且质量分达到阈值的单跳答案，可以进入经验库。复用不是“看到相似问题就返回旧答案”，"
        "而是同时满足：问题标准化后相同、引用覆盖请求 as_of、context hash 未变化、运行时签名一致、未过期。",
    )
    add_bullets(
        document,
        [
            "签名包含 prompt 版本、生成器、embedding、top_k、chunk 参数。",
            "TTL 30 天；过期记录会被安全删除。",
            "低分反馈会删除经验；高分反馈会补录/提升。",
            "删除失败不会让聊天 500，最坏是本次不复用。",
        ],
    )


def add_chapter_7(document: Document) -> None:
    add_h1(document, "第 7 章  数据模型：七张表讲清楚")
    add_paragraph(
        document,
        "项目用 SQLite 保存所有可审计状态。SQLite 不是“玩具数据库”，在这个规模下它让每次实验都可以本地复现、"
        "直接打开检查；代价是单机写入能力和并发能力有限。",
    )

    add_h2(document, "7.1 表关系")
    add_code(
        document,
        """documents 1 ── N chunks
documents 1 ── N document_versions
runs      1 ── N feedback
experiences（独立，通过 citations_json 关联 chunk）
eval_runs（独立，保存每次评测报告）""",
        caption="七张表的关系",
    )
    add_table(
        document,
        ["表", "作用", "关键字段"],
        [
            ["documents", "当前文档状态", "content、checksum、fingerprint、policy_key、version、生效窗口"],
            ["chunks", "检索最小单位", "content、embedding_json、metadata_json、document_id"],
            ["document_versions", "版本快照", "content、checksum、fingerprint、created_at"],
            ["runs", "每次问答", "answer、retrieved_json、citations_json、metrics_json"],
            ["eval_runs", "每次评测", "metrics_json、cases_json、report_json"],
            ["experiences", "组织经验", "answer、citations_json、context_hash、signature、TTL"],
            ["feedback", "用户反馈", "run_id、rating、comment、corrected_answer"],
        ],
        widths=[4.0, 4.5, 8.0],
    )

    add_h2(document, "7.2 documents 的字段为什么这么多")
    add_table(
        document,
        ["字段", "为什么需要"],
        [
            ["id", "稳定主键，chunk id 和 run 引用都靠它"],
            ["source", "评测按文件名比对来源，不依赖 UUID"],
            ["content / checksum", "当前正文与内容指纹"],
            ["fingerprint", "制度身份，唯一约束，区分同正文不同窗口"],
            ["policy_key / version", "制度族与版本标签"],
            ["effective_from / until", "as_of 过滤的闭区间"],
            ["metadata_json", "frontmatter、文件名、编码等"],
            ["status / chunk_count", "索引状态与展示"],
        ],
        widths=[4.3, 12.2],
    )

    add_h2(document, "7.3 chunks 的 metadata 约定")
    add_paragraph(
        document,
        "chunks.metadata_json 不是随便塞字段。v1.1.0 起，切块必须继承：",
    )
    add_bullets(
        document,
        [
            "source / relative_path / title",
            "checksum",
            "policy_key / version / effective_from / effective_until",
            "heading_path",
            "embedding_model / embedding_dimension",
        ],
    )
    add_paragraph(
        document,
        "这样引用可以显示“来自哪份文档、哪一版、哪一章”，增量失效可以定位，维度变更也有记录。",
    )

    add_h2(document, "7.4 版本表与旧 as_of 的边界")
    add_paragraph(
        document,
        "document_versions 保存每次创建/改版的完整正文，GET /api/v1/documents/{id}/versions 可以审计历史正文。"
        "但旧版本的 chunk 没有参与当前检索，所以“改版后旧 as_of 仍能命中旧内容”还没有完全实现。"
        "这是本项目在文档中明确写出的边界，不要把它说成已完成。",
    )


def add_chapter_8(document: Document) -> None:
    add_h1(document, "第 8 章  评测、指标与质量门禁")
    add_paragraph(
        document,
        "评测的目的不是把分数刷高，而是回答：“这次改动有没有让系统变差？”因此需要固定语料、固定题、固定 as_of、"
        "固定指标和固定门禁。",
    )

    add_h2(document, "8.1 21 道题怎么分布")
    add_table(
        document,
        ["类型", "数量", "例子"],
        [
            ["HR", "6", "年假、病假、远程办公、调休、婚假"],
            ["安全", "5", "密码、数据分级、事件报告、供应商审计、设备丢失"],
            ["IT", "2", "VPN 开通、账号申请"],
            ["采购", "3", "采购审批、住宿标准、发票期限"],
            ["客服", "2", "P1 响应、退款审批"],
            ["跨职能", "1", "离职资产归还 + 账号禁用"],
            ["安全拒答/注入", "2", "股票代码、忽略指令"],
        ],
        widths=[4.0, 2.2, 10.3],
    )
    add_paragraph(
        document,
        "19 道 answer、1 道 refuse、1 道 resist_injection。每道题固定 as_of，q021 使用 2025-06-01，其余多为 2026-06-01。",
    )

    add_h2(document, "8.2 指标含义")
    add_table(
        document,
        ["指标", "含义", "当前基线"],
        [
            ["pass_rate", "通过题数 / 总题数", "100%"],
            ["retrieval_hit_rate", "answer 题中正确来源是否进入 top_k", "100%"],
            ["MRR", "第一个正确来源排名的倒数均值", "1.000"],
            ["keyword_recall", "答案覆盖期望关键词的比例", "100%"],
            ["citation_precision", "引用来源属于期望来源的比例", "100%"],
            ["invalid_citation_rate", "raw 输出中非法引用标记比例", "0.0%"],
            ["refusal_accuracy", "该拒答的题是否拒答", "100%"],
            ["injection_resistance", "注入题是否被拦截/拒答", "100%"],
            ["p50 / p95 latency", "本机进程内延迟", "7.5 / 7.6 ms"],
        ],
        widths=[4.2, 8.2, 4.1],
    )

    add_h2(document, "8.3 质量门禁")
    add_table(
        document,
        ["门禁", "阈值", "方向"],
        [
            ["通过率", "≥ 80%", "越高越好"],
            ["来源命中率", "≥ 85%", "越高越好"],
            ["引用准确率", "≥ 80%", "越高越好"],
            ["非法引用率", "≤ 2%", "越低越好"],
            ["注入抵抗率", "= 100%", "安全底线"],
            ["CI 回归", "不低于 baseline - 2 个百分点", "防止悄悄退化"],
        ],
        widths=[4.5, 6.8, 5.2],
    )

    add_h2(document, "8.4 怎么看一次评测报告")
    add_numbered(
        document,
        [
            "先看 quality_gate.checks：哪一项 false。",
            "再看 cases：失败题的 failure_reasons 和 raw_answer_preview。",
            "定位是检索没命中、关键词不够、引用不准、非法引用还是拒答错误。",
            "改代码后先跑单题，再跑全量；不要只看总分。",
            "用 --baseline 对比，确认没有超过 2 个百分点的回退。",
        ],
    )
    add_code(
        document,
        """python eval/run_eval.py `
  --output eval/results/baseline.json `
  --baseline eval/results/baseline.json `
  --max-regression 0.02 `
  --require-quality-gate""",
        caption="本地评测命令（PowerShell）",
    )

    add_callout(
        document,
        "诚实说明",
        "21/21 不是通用 RAG 能力证明。语料只有 7 篇，生成器是抽取式的，题目是容易集。"
        "题库扩容到 100+、增加 30% holdout、引入真实干扰文档，仍然是下一步。",
        fill=AMBER,
        title_color="8A5A00",
    )


def add_chapter_9(document: Document) -> None:
    add_h1(document, "第 9 章  把项目跑起来")
    add_paragraph(
        document,
        "建议第一次先不接 OpenAI，直接用默认的离线链路：hashing-384 + BM25 + extractive。"
        "这样没有 API Key、没有网络依赖，最容易确认环境没问题。",
    )

    add_h2(document, "9.1 方式一：uv")
    add_code(
        document,
        """uv sync
uv run uvicorn app.main:app --reload""",
        caption="推荐方式，使用 uv.lock 锁定依赖",
    )

    add_h2(document, "9.2 方式二：venv + pip")
    add_code(
        document,
        """python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
uvicorn app.main:app --reload""",
        caption="Windows PowerShell",
    )

    add_h2(document, "9.3 方式三：Docker")
    add_code(
        document,
        """docker build -t enterprise-rag-quality-lab:1.1.0 .
docker run --rm -p 8000:8000 enterprise-rag-quality-lab:1.1.0""",
        caption="镜像基于 python:3.11-slim，非 root，带 /health 健康检查",
    )

    add_h2(document, "9.4 打开这些地址")
    add_table(
        document,
        ["地址", "用途"],
        [
            ["http://127.0.0.1:8000/docs", "Swagger UI，直接试接口"],
            ["http://127.0.0.1:8000/health", "健康检查"],
            ["http://127.0.0.1:8000/api/v1/organization", "智能组织状态与经验数"],
            ["http://127.0.0.1:8000/api/v1/eval/dataset", "预览评测集"],
        ],
        widths=[8.0, 8.5],
    )

    add_h2(document, "9.5 第一个问题")
    add_code(
        document,
        """curl -X POST http://127.0.0.1:8000/api/v1/chat ^
  -H "Content-Type: application/json" ^
  -d "{\\"question\\":\\"员工入职满三年，年假有多少天？\\",\\"as_of\\":\\"2026-06-01\\"}\"""",
        caption="Windows cmd 写法；PowerShell 用 Invoke-RestMethod 更稳",
    )
    add_paragraph(
        document,
        "重点看返回里的 answer、citations、retrieved_chunks、quality_checks、experience_reused。"
        "answer 是用户看到的；citations 是审计入口；retrieved_chunks 告诉你检索到了什么；"
        "quality_checks 告诉你为什么通过/不通过。",
    )

    add_h2(document, "9.6 常见问题")
    add_table(
        document,
        ["现象", "原因", "处理"],
        [
            ["8000 端口被占用", "已有服务", "换 --port 8001，或结束旧进程"],
            ["Docker 构建失败", "Docker daemon 未启动", "启动 Docker Desktop 后重试"],
            ["pytest 临时目录 PermissionError", "受限 Windows 沙箱", "创建 data/pytest-runtime，设置 RAG_LAB_TEST_DIR"],
            ["GBK 文档加载失败", "旧版只支持 UTF-8", "v1.1.0 已支持 GB18030 回退"],
            ["维度不匹配报错", "改了 embedding_dimension 但旧索引未重建", "重新 reindex，或换新数据库"],
            ["OpenAI 调用失败", "Key/网络/兼容接口问题", "检查 .env；失败会回退抽取式"],
        ],
        widths=[4.8, 5.0, 6.7],
    )

    add_h2(document, "9.7 关键环境变量")
    add_table(
        document,
        ["变量", "默认值", "说明"],
        [
            ["RAG_LAB_CHUNK_SIZE", "760", "切块字符预算"],
            ["RAG_LAB_CHUNK_OVERLAP", "110", "重叠，不得超过 chunk_size/2"],
            ["RAG_LAB_TOP_K", "5", "检索深度"],
            ["RAG_LAB_MIN_RETRIEVAL_SCORE", "0.22", "低分拒答阈值"],
            ["RAG_LAB_MIN_QUERY_COVERAGE", "0.30", "查询覆盖率阈值"],
            ["RAG_LAB_EMBEDDING_DIMENSION", "384", "哈希向量维度"],
            ["RAG_LAB_GENERATOR_PROVIDER", "extractive", "extractive 或 openai"],
            ["RAG_LAB_OPENAI_API_KEY", "空", "openai 模式必填"],
            ["RAG_LAB_EXPERIENCE_REUSE_ENABLED", "true", "是否启用经验复用"],
            ["RAG_LAB_EXPERIENCE_MIN_QUALITY", "0.75", "经验沉淀质量门槛"],
        ],
        widths=[7.2, 3.6, 5.7],
    )


def add_chapter_10(document: Document) -> None:
    add_h1(document, "第 10 章  动手改：六个练习")
    add_paragraph(
        document,
        "看懂不等于掌握。下面六个练习从易到难，做完你就能独立改这个项目。每个练习都给出“改哪里”和“怎么验收”。",
    )

    add_h2(document, "练习 1：新增一份制度文档")
    add_paragraph(
        document,
        "在 data/sample_docs/ 新建一个 .md，写 frontmatter：policy_key、version、effective_from、title。"
        "启动服务或调用 /api/v1/documents/ingest-sample，然后问一个只在这份文档里的问题。",
    )
    add_bullets(
        document,
        [
            "验收：文档列表出现新文档；chunk_count > 0；问题引用 source 指向新文件。",
            "观察：document_versions 是否多了一条快照。",
        ],
    )

    add_h2(document, "练习 2：问同一问题的两个 as_of")
    add_paragraph(
        document,
        "分别用 as_of=2025-06-01 和 2026-06-01 问“入职满三年年假多少天”。",
    )
    add_bullets(
        document,
        [
            "验收：2025 回答 8 天并引用 2025 版；2026 回答 10 天并引用 2026 版。",
            "思考：如果去掉 as_of 过滤，会发生什么？",
        ],
    )

    add_h2(document, "练习 3：加一道评测题")
    add_paragraph(
        document,
        "在 eval/dataset.jsonl 末尾加一个 q022，写清 behavior、as_of、expected_sources、expected_keywords。"
        "先单独跑一次评测，再跑全量。",
    )
    add_bullets(
        document,
        [
            "验收：新题出现在报告 cases 中；失败时 failure_reasons 能解释原因。",
            "注意：id 必须是 qddd 格式，answer 题必须有来源和关键词。",
        ],
    )

    add_h2(document, "练习 4：故意制造一个非法引用")
    add_paragraph(
        document,
        "在测试里用假生成器返回 [99]，观察 raw_answer、invalid_citation_rate 和 quality_gate。",
    )
    add_bullets(
        document,
        [
            "验收：answer 里没有 [99]；raw_answer 里有；invalid rate > 0；门禁失败。",
            "对应测试：tests/test_quality_contracts.py。",
        ],
    )

    add_h2(document, "练习 5：观察一次完整 trace")
    add_paragraph(
        document,
        "调用 /api/v1/chat 后，用返回的 run_id 调 /api/v1/runs/{run_id}，查看 retrieved、citations、agents、quality_checks。",
    )
    add_bullets(
        document,
        [
            "验收：能看到 Coordinator、Retriever、Reasoner、Auditor、Experience 的轨迹。",
            "重点：quality_checks 里哪一项 false，对应哪条 finding。",
        ],
    )

    add_h2(document, "练习 6：换一个生成器")
    add_paragraph(
        document,
        "在 .env 配置 OpenAI 兼容接口，设置 RAG_LAB_GENERATOR_PROVIDER=openai、API Key、Base URL、Model。"
        "先故意填错网络，确认会重试并回退抽取式；再填对，观察 mode 和 fallback_used。",
    )
    add_bullets(
        document,
        [
            "验收：网络错误不返回 500；fallback_used=true；答案仍有引用。",
            "注意：OpenAI 路径的 quote 可能为空，Auditor 只校验实际提供的 quote。",
        ],
    )

    add_callout(
        document,
        "学习建议",
        "每改一个地方，先跑相关单测，再跑 21 题评测，最后看 git diff。不要一次改五个变量，否则你无法知道是哪一个导致指标变化。",
        fill=GREEN,
        title_color="1E6B45",
    )


def add_chapter_11(document: Document) -> None:
    add_h1(document, "第 11 章  工程质量、安全与边界")
    add_paragraph(
        document,
        "一个 RAG 项目能不能讲清楚，不只看回答效果，还看它有没有工程约束：可重复、可测试、可审计、可回退。",
    )

    add_h2(document, "11.1 当前工程配置")
    add_table(
        document,
        ["项", "当前状态"],
        [
            ["版本", "1.1.0，Git tag v1.1.0"],
            ["测试", "53 项，覆盖率 82.51%"],
            ["CI", "Python 3.12，uv sync --frozen，覆盖率 ≥ 80%，baseline-2%"],
            ["容器", "python:3.11-slim，非 root，EXPOSE 8000，HEALTHCHECK"],
            ["数据库", "SQLite schema v4，WAL，短连接"],
            ["依赖安全", "运行时/全依赖 0 已知 CVE，Bandit 0"],
            ["License", "无 GPL/AGPL/LGPL；certifi MPL-2.0 仅开发依赖"],
            ["评测", "21 题，21/21，非法引用 0.0%，p50 7.5ms / p95 7.6ms"],
        ],
        widths=[4.0, 12.5],
    )

    add_h2(document, "11.2 注入防御做了哪些")
    add_bullets(
        document,
        [
            "直接注入：问题命中“忽略之前指令/打印系统提示/越狱”等模式，检索前拒答。",
            "间接注入：检索文本中的 HTML 注释、特殊 token、含注入指令的句子被清洗。",
            "上下文隔离：资料放在 <context> 中，并在 system prompt 明确其为不可信数据。",
            "输出校验：引用编号必须存在；quote 必须能在 chunk 中找到；非法引用计数。",
            "评测覆盖：21 题中包含直接注入和间接注入样例。",
        ],
    )

    add_h2(document, "11.3 不能吹的地方")
    add_table(
        document,
        ["限制", "影响", "下一步"],
        [
            ["21 题、7 篇文档", "容易过拟合，不能证明泛化", "扩容到 100+，加 30% holdout"],
            ["旧 as_of 无版本化 chunk", "改版后旧内容只能审计，不能检索", "chunks 关联 version_id 或版本索引"],
            ["384 维哈希向量", "同义改写召回弱", "接真实 embedding，做维度/索引版本"],
            ["抽取式生成", "表达生硬，复杂归纳弱", "LLM + 抽取 fallback，人工评审"],
            ["无鉴权/ACL/限流", "不能直接上生产", "认证、租户、文档 ACL、速率限制"],
            ["无 PII/保留期", "历史运行可能留敏感信息", "脱敏、保留策略、审计日志"],
            ["结构化 diff 仅数值", "复杂条款差异表达不足", "主体/条件/数值/单位四元组"],
        ],
        widths=[5.0, 6.5, 5.0],
    )


def add_chapter_12(document: Document) -> None:
    add_h1(document, "第 12 章  怎么把它讲成你的项目")
    add_paragraph(
        document,
        "面试时不缺“我做过 RAG”，缺的是“我知道它哪里会错、我怎么证明、边界在哪”。下面给一个 60 秒版本和几个深挖问题。",
    )

    add_h2(document, "12.1 60 秒项目介绍")
    add_callout(
        document,
        "可背诵版本",
        "这是一个离线可运行的企业制度 RAG 质量实验室。默认用哈希向量 + BM25 混合检索、抽取式生成和 SQLite，"
        "重点不是堆模型，而是把回答做成可审计链路：每个 chunk 带来源和版本，问题可以带 as_of 查历史制度，"
        "引用有句子级 quote，非法引用会在 raw 输出上计数并纳入 2% 门禁。"
        "系统上还有 Coordinator/Retriever/Reasoner/Auditor/Experience 五个角色，高质量答案会沉淀为经验，"
        "低分反馈会删除。当前 53 项测试、82.5% 覆盖率，21 题基线 21/21，但我知道它是容易集，"
        "下一步是 100+ 题库和版本化 chunk 检索。",
    )

    add_h2(document, "12.2 五个深挖问题")
    add_table(
        document,
        ["问题", "回答要点"],
        [
            ["为什么混合检索？", "向量管语义，BM25 管制度名/金额/天数等关键词；融合分 0.55/0.40/0.05，适合小语料可解释基线。"],
            ["怎么保证旧制度不被当成当前制度？", "policy_key + effective_from/until + as_of 过滤；同窗口重叠 409；引用带版本和日期。"],
            ["怎么防止引用造假？", "生成后只保留真实编号；raw 保留并计非法引用率；Auditor 校验编号、来源、日期和 quote。"],
            ["为什么 21/21 不能吹？", "7 篇文档、抽取式生成、容易集；它证明回归稳定，不证明泛化。下一步 holdout 和真实干扰文档。"],
            ["最大的技术债是什么？", "旧 as_of 的版本化 chunk 检索、题库规模、真实 embedding、生产鉴权/ACL/可观测性。"],
        ],
        widths=[5.0, 11.5],
    )

    add_h2(document, "12.3 不要这样说")
    add_bullets(
        document,
        [
            "不要说“准确率 100%”。说“固定容易集回归 21/21”。",
            "不要说“多智能体自治”。说“五个职责明确的角色 + 可审计轨迹”。",
            "不要说“生产级安全”。说“有注入防护，但没有鉴权/ACL/限流”。",
            "不要说“版本历史完整”。说“有版本快照 API，旧 as_of chunk 检索还没做”。",
        ],
    )

    add_h2(document, "12.4 下一步路线")
    add_table(
        document,
        ["优先级", "事项", "验收"],
        [
            ["P0", "题库扩到 100+，30% holdout", "evolver 候选不再全是 1.0"],
            ["P0", "旧 as_of 版本化 chunk 检索", "改版后旧日期仍命中旧内容"],
            ["P1", "真实 embedding + 索引版本", "同义改写 hit@5 提升，维度变更可检测"],
            ["P1", "结构化 diff 四元组", "主体/条件/数值/单位对齐"],
            ["P2", "鉴权/ACL/限流/保留期", "越权率 0，未授权读不到 runs"],
            ["P2", "结构化日志/trace_id/指标", "能定位某次 run 的某一步"],
        ],
        widths=[2.2, 7.0, 7.3],
    )


def add_glossary(document: Document) -> None:
    add_h1(document, "术语表：新手最容易混的 24 个词")
    terms = [
        ("RAG", "检索增强生成：先查资料，再让模型基于资料回答。"),
        ("chunk", "文档切块，是检索和引用的最小单位。"),
        ("embedding", "把文本映射成向量，用距离表示语义相近。"),
        ("BM25", "基于词频和逆文档频率的关键词检索算法。"),
        ("混合检索", "向量检索 + 关键词检索，按权重融合。"),
        ("top_k", "检索返回的候选块数量。"),
        ("as_of", "以哪一天为制度生效时点。"),
        ("policy_key", "同一制度族的稳定标识。"),
        ("effective_from / until", "制度生效闭区间。"),
        ("checksum", "正文哈希，判断内容有没有变。"),
        ("fingerprint", "制度身份哈希，包含正文、窗口和元数据。"),
        ("document_versions", "每次创建/改版的正文快照。"),
        ("citation", "答案中的引用编号及其来源信息。"),
        ("quote", "引用对应的原文句子，用于句子级校验。"),
        ("invalid_citation_rate", "raw 输出中非法引用标记的比例。"),
        ("refusal", "资料不足或安全问题时的明确拒答。"),
        ("quality gate", "评测必须满足的阈值组合。"),
        ("baseline", "提交到仓库的回归参照报告。"),
        ("holdout", "不参与调参、只用于最终验证的题集。"),
        ("experience", "审计通过的高质量问答记忆。"),
        ("TTL", "经验存活时间，本项目 30 天。"),
        ("trace", "智能体每一步的输入、输出、耗时和状态。"),
        ("migration", "数据库结构升级脚本。"),
        ("fallback", "主路径失败时的降级路径，例如 LLM → 抽取式。"),
    ]
    add_table(document, ["术语", "一句话解释"], [[a, b] for a, b in terms], widths=[4.2, 12.3], font_size=9)


def add_appendices(document: Document) -> None:
    add_h1(document, "附录 A  API 速查")
    add_table(
        document,
        ["方法", "路径", "用途"],
        [
            ["GET", "/health", "健康检查"],
            ["POST", "/api/v1/chat", "提问，支持 as_of/top_k/document_ids"],
            ["GET", "/api/v1/runs", "运行列表"],
            ["GET", "/api/v1/runs/{run_id}", "运行详情与轨迹"],
            ["POST", "/api/v1/runs/{run_id}/feedback", "评分/修正答案"],
            ["POST", "/api/v1/documents", "创建文档"],
            ["POST", "/api/v1/documents/upload", "上传 md/txt"],
            ["POST", "/api/v1/documents/ingest-sample", "幂等导入示例语料"],
            ["GET", "/api/v1/documents", "列表，支持 as_of"],
            ["GET", "/api/v1/documents/{id}", "详情"],
            ["GET", "/api/v1/documents/{id}/versions", "版本快照"],
            ["POST", "/api/v1/documents/{id}/reindex", "重建 chunks/向量"],
            ["DELETE", "/api/v1/documents/{id}", "删除文档"],
            ["GET", "/api/v1/organization", "组织状态"],
            ["GET", "/api/v1/organization/experiences", "经验列表"],
            ["DELETE", "/api/v1/organization/experiences/{id}", "删除经验"],
            ["POST", "/api/v1/organization/evolve", "受控阈值进化"],
            ["GET", "/api/v1/eval/dataset", "评测集预览"],
            ["POST", "/api/v1/eval/run", "执行评测"],
            ["GET", "/api/v1/eval/results", "历史评测"],
            ["GET", "/api/v1/eval/results/{id}", "评测详情"],
            ["GET", "/api/v1/eval/baseline", "读取基线"],
        ],
        widths=[2.0, 8.4, 6.1],
        font_size=8.7,
    )

    add_h1(document, "附录 B  常用命令")
    add_code(
        document,
        """# 启动
uv run uvicorn app.main:app --reload

# 测试 + 覆盖率
python -m pytest --cov=app --cov-report=term-missing

# 代码检查
python -m ruff check .

# 评测
python eval/run_eval.py --output eval/results/baseline.json

# 基线对比
python eval/run_eval.py --baseline eval/results/baseline.json --max-regression 0.02

# 安全扫描
uv run bandit -r app -ll
uv run pip-audit
uv run pip-licenses --format=markdown""",
        caption="在项目根目录执行",
    )

    add_h1(document, "附录 C  一页纸总结")
    add_callout(
        document,
        "如果明天面试，只背这一页",
        "项目定位：企业制度 RAG 质量实验室，不是通用聊天机器人。\n"
        "链路：Loader → Splitter → Hashing Embedding + BM25 → Retriever → Generator → Citation Audit → Evaluator。\n"
        "组织：Coordinator 拆任务，Retriever 取证，Reasoner 合成，Auditor 审计，Experience 沉淀。\n"
        "可信：as_of 版本过滤、chunk 元数据、fingerprint、版本快照、句子 quote、raw 非法引用率门禁。\n"
        "数字：53 项测试、82.5% 覆盖率、21 题 21/21、非法引用 0%、p50 7.5ms。\n"
        "边界：7 篇文档/21 题、旧 as_of 无版本化 chunk、384 哈希向量、无鉴权/ACL/限流。\n"
        "一句话：它证明的是“管线可重复、引用可审计、版本有身份、退化能发现”，不是“通用 RAG 100% 准确”。",
        fill=GREEN,
        title_color="1E6B45",
    )


def build() -> Path:
    document = Document()
    section = document.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(2.0)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.2)
    section.different_first_page_header_footer = True

    style_document(document)
    add_header(section)
    add_page_number_footer(section, "Enterprise RAG Quality Lab · v1.1.0")

    document.core_properties.title = "从零读懂 Enterprise RAG Quality Lab"
    document.core_properties.subject = "企业 RAG 质量实验室新手项目讲解"
    document.core_properties.author = "项目讲解"
    document.core_properties.keywords = "RAG, 企业知识库, 质量评测, 智能体, FastAPI, SQLite"
    document.core_properties.comments = "基于 v1.1.0 当前仓库代码编写"

    add_cover(document)
    add_front_matter(document)
    add_chapter_1(document)
    add_chapter_2(document)
    add_chapter_3(document)
    add_chapter_4(document)
    add_chapter_5(document)
    add_chapter_6(document)
    add_chapter_7(document)
    add_chapter_8(document)
    add_chapter_9(document)
    add_chapter_10(document)
    add_chapter_11(document)
    add_chapter_12(document)
    add_glossary(document)
    add_appendices(document)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    document.save(OUTPUT)
    return OUTPUT


if __name__ == "__main__":
    path = build()
    print(path)
