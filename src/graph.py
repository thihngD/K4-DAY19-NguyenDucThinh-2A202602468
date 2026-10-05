"""Knowledge Graph (Neo4j) + GraphRAG over two drug-topic knowledge bases.

Contract (fixed — bench_kg.py and the tests rely on it):
    link_entity(name, known)                       -> one of `known` or None          (TODO KG-1)
    build_graph(graph, law_docs, news_docs, llm_fn)   load both KBs into Neo4j      (TODO KG-2)
        every node created from ONE document carries the property `doc_id`
    Neo4jGraph.context(question, doc_ids)         -> list[str] facts               (TODO KG-3)
    GraphRAGAgent.answer(question, top_k)         -> str                           (TODO KG-4)

Everything else in this file is a HINT: one possible ontology (below). Use it as is, change it,
or design your own — your own ontology + report/ONTOLOGY.md earns the bonus (see SUBMISSION.md).

Suggested ontology (Crime is the bridge between the law KB and the news KB):

    (:Article {id, title, law, doc_id})-[:DEFINES]->(:Crime {name})
    (:Article)-[:HAS_CLAUSE]->(:Clause {id, number, penalty, text})-[:MENTIONS]->(:Substance {name})
    (:Case {name, summary, date, doc_id})-[:CHARGED_WITH]->(:Crime)
    (:Case)-[:INVOLVES {amount}]->(:Substance)
    (:Case)-[:LOCATED_IN]->(:Location {name})
    (:Person {name, aliases})-[:INVOLVED_IN {role, sentence, charge}]->(:Case)
"""

from __future__ import annotations

import difflib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from .models import Document
from .store import EmbeddingStore

# Canonical substance names: the ones BLHS Chương XX lists, plus common ones in Vietnamese news.
SUBSTANCES = ["Heroine", "Cocaine", "Methamphetamine", "Amphetamine", "MDMA", "XLR-11", "Ketamine",
              "cần sa", "thuốc phiện", "côca"]
CLAUSE_START = re.compile(r"^(\d+)\.\s", re.MULTILINE)
FOOTNOTE = re.compile(r"\[\d+\]")

def load_markdown_docs(folder: str | Path) -> list[Document]:
    """Read crawler output (.md with a flat `key: "value"` front matter) into Documents."""
    docs = []
    for path in sorted(Path(folder).glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        _, front, body = raw.split("---", 2)
        metadata = {k: json.loads(v) for k, v in re.findall(r'^(\w+): (".*")$', front, re.MULTILINE)}
        docs.append(Document(id=metadata.get("doc_id", path.stem), content=body.strip(), metadata=metadata))
    return docs

def normalize_crime(name: str) -> str:
    """'Tội Mua bán trái phép chất ma túy' -> 'mua bán trái phép chất ma túy'."""
    name = re.sub(r"\s+", " ", name.strip().strip("\"'“”").lower())
    return name.removeprefix("tội ").strip()

def link_entity(name: str, known: list[str], normalize: Callable[[str], str] = normalize_crime) -> str | None:
    """Map a free-text mention (e.g. a charge written by a journalist) onto one canonical name in `known`."""
    target = normalize(name)
    if not target:
        return None
    by_normalized = {normalize(candidate): candidate for candidate in known}
    if target in by_normalized:
        return by_normalized[target]
    close = difflib.get_close_matches(target, list(by_normalized), n=1, cutoff=0.8)
    return by_normalized[close[0]] if close else None

def find_substances(text: str) -> list[str]:
    # Whole-word match: plain substring would make "Amphetamine" match inside "Methamphetamine".
    return [name for name in SUBSTANCES
            if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, flags=re.IGNORECASE)]

# ----------------------------------------------------------------------------------------------
# Which ontology build_graph / context use. "own" = our bonus ontology (default, see report/ONTOLOGY.md);
# "hint" = the suggested ontology, kept so the two can be benchmarked side by side:
#     KG_ONTOLOGY=hint python bench_kg.py --judge --out ket_qua_benchmark_kg.hint.txt
# ----------------------------------------------------------------------------------------------
ONTOLOGY = os.getenv("KG_ONTOLOGY", "own")

# Own ontology, change 1 (fixes E3): one canonical name per substance before MERGE.
SUBSTANCE_ALIASES = {"heroin": "Heroine", "ketamin": "Ketamine", "thuốc lắc": "MDMA", "ma túy đá": "Methamphetamine",
                     "ma tuý đá": "Methamphetamine", "meth": "Methamphetamine"}
GENERIC_DRUGS = {"ma túy", "ma tuý", "chất ma túy", "chất ma tuý", "ma túy tổng hợp", "ma tuý tổng hợp"}

def canonical_substance(name: str) -> str | None:
    """'ketamine' -> 'Ketamine', 'heroin' -> 'Heroine'; generic words ('ma túy') -> None (not a substance)."""
    key = re.sub(r"\s+", " ", name.strip().lower())
    if not key or key in GENERIC_DRUGS:
        return None
    if key in SUBSTANCE_ALIASES:
        return SUBSTANCE_ALIASES[key]
    for known in SUBSTANCES:
        if known.lower() == key:
            return known
    return key[:1].upper() + key[1:]

# Own ontology, change 2 (fixes E2): penalty bounds stored on Clause, so "khung cao nhất" is a query, not text.
PRISON = re.compile(r"\btù\b")
YEARS = re.compile(r"(\d+)\s*năm")

def parse_penalty(penalty: str) -> dict[str, Any]:
    """'phạt tù từ 02 năm đến 07 năm' -> min 2, max 7; 'phạt tù 20 năm hoặc tù chung thân' -> max 20, life."""
    if not PRISON.search(penalty):                               # fines / bans only: no prison bounds
        return {"min_years": None, "max_years": None, "life_or_death": False}
    years = [int(y) for y in YEARS.findall(penalty)]
    return {"min_years": years[0] if years else None,
            "max_years": max(years) if years else None,
            "life_or_death": bool(re.search(r"chung thân|tử hình", penalty))}

# ----------------------------------------------------------------------------------------------
# HINT — suggested ontology: extraction helpers
# ----------------------------------------------------------------------------------------------

def parse_law_article(doc: Document) -> dict[str, Any]:
    """Deterministic (regex) extraction for one 'Điều' — law text is regular enough to skip the LLM."""
    article_id = doc.metadata["article"]                       # "Điều 251 BLHS"
    title = doc.metadata["title"].split(". ", 1)[-1]           # "Tội mua bán trái phép chất ma túy"
    body = FOOTNOTE.sub("", doc.content)
    starts = list(CLAUSE_START.finditer(body))
    clauses = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(body)
        text = body[start.start():end].strip()
        first_line = text.splitlines()[0]
        penalty = re.search(r"\bbị ((?:phạt|tù|cảnh cáo).+?)(?::|$)", first_line)
        clauses.append({
            "id": f"{article_id} khoản {start.group(1)}",
            "number": int(start.group(1)),
            "penalty": penalty.group(1).rstrip(".") if penalty else "",
            "text": text,
            "substances": find_substances(text),
            **parse_penalty(penalty.group(1) if penalty else ""),
        })
    return {
        "id": article_id,
        "law": doc.metadata.get("law", ""),
        "title": title,
        "doc_id": doc.id,
        "crime": normalize_crime(title) if title.startswith("Tội ") else None,
        "clauses": clauses,
    }

NEWS_EXTRACTION_PROMPT = """Bạn trích xuất knowledge graph từ một bài báo tiếng Việt về ma túy.
Chỉ dùng thông tin có trong bài. Trả về JSON đúng dạng:
{{"cases": [{{
  "name": "tên ngắn của vụ việc, ví dụ: Vụ mua bán 36kg ma túy tại TP.HCM",
  "summary": "1-2 câu tóm tắt",
  "date": "ngày xảy ra/xét xử nếu có, dạng YYYY-MM-DD hoặc chuỗi rỗng",
  "location": "tỉnh/thành phố, chuỗi rỗng nếu không rõ",
  "charges": ["tội danh, BẮT BUỘC chọn đúng nguyên văn từ DANH SÁCH TỘI DANH"],
  "substances": [{{"name": "tên chất, dùng tên chuẩn trong DANH SÁCH CHẤT nếu khớp", "amount": "khối lượng nếu có"}}],
  "people": [{{"name": "họ tên", "aliases": ["biệt danh"], "role": "bị cáo|bị can|nghi phạm|người liên quan|cán bộ",
               "charge": "tội danh của người này (từ DANH SÁCH TỘI DANH) hoặc chuỗi rỗng",
               "sentence": "mức án nếu có, ví dụ: tử hình, 8 năm tù"}}]
}}]}}
Bài không nói về vụ việc cụ thể (tuyên truyền, hội nghị...) thì trả về {{"cases": []}}.

DANH SÁCH TỘI DANH: {crimes}
DANH SÁCH CHẤT: {substances}

Tiêu đề: {title}
Nội dung:
{content}"""

def extract_news_cases(doc: Document, llm_fn: Callable[[str], str], known_crimes: list[str]) -> list[dict]:
    """LLM extraction for one news article; charges are re-linked to law-KB crimes in code."""
    prompt = NEWS_EXTRACTION_PROMPT.format(
        crimes="; ".join(known_crimes), substances=", ".join(SUBSTANCES),
        title=doc.metadata.get("title", ""), content=doc.content[:12000],
    )
    try:
        cases = json.loads(llm_fn(prompt)).get("cases", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    for case in cases:
        case["charges"] = sorted({c for c in (link_entity(x, known_crimes) for x in case.get("charges", [])) if c})
        for person in case.get("people", []):
            person["charge"] = link_entity(person.get("charge") or "", known_crimes) or ""
    return cases

# ----------------------------------------------------------------------------------------------
# Neo4j
# ----------------------------------------------------------------------------------------------

class Neo4jGraph:
    """Thin wrapper over the official neo4j driver."""

    def __init__(self, uri: str, user: str, password: str) -> None:
        from neo4j import GraphDatabase

        self.driver = GraphDatabase.driver(uri, auth=(user, password), notifications_min_severity="OFF")
        self.driver.verify_connectivity()

    def close(self) -> None:
        self.driver.close()

    def run(self, cypher: str, **params: Any) -> list[dict]:
        records, _, _ = self.driver.execute_query(cypher, params)
        return [record.data() for record in records]

    def reset(self) -> None:
        """Delete every node, relationship and constraint (bench_kg.py calls this before build_graph)."""
        self.run("MATCH (n) DETACH DELETE n")
        for row in self.run("SHOW CONSTRAINTS YIELD name RETURN name"):
            self.run(f"DROP CONSTRAINT `{row['name']}` IF EXISTS")

    def stats(self) -> dict[str, int]:
        nodes = self.run("MATCH (n) RETURN count(n) AS n")[0]["n"]
        rels = self.run("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]
        return {"nodes": nodes, "relationships": rels}

    def seed_facts(self, question: str, doc_ids: list[str], skip_labels: tuple[str, ...] = (),
                   limit: int = 60) -> tuple[list[str], list[str]]:
        """Ontology-independent first step: seed nodes + their 1-hop edges as text facts.

        Seeds = nodes whose `doc_id` is in doc_ids, or whose `name`/`aliases` appear in the question.
        Returns (seed elementIds, facts). Nodes with a label in skip_labels are left out of the facts.
        """
        seeds = self.run(
            """
            MATCH (n)
            WHERE n.doc_id IN $doc_ids
               OR (n.name IS :: STRING AND size(n.name) >= 3 AND toLower($q) CONTAINS toLower(n.name))
               OR any(a IN coalesce(n.aliases, []) WHERE size(a) >= 3 AND toLower($q) CONTAINS toLower(a))
            RETURN elementId(n) AS id
            """,
            q=question, doc_ids=doc_ids,
        )
        seed_ids = [row["id"] for row in seeds]
        edges = self.run(
            """
            MATCH (s)-[r]-(m)
            WHERE elementId(s) IN $ids
              AND none(l IN labels(s) + labels(m) WHERE l IN $skip)
            WITH DISTINCT r LIMIT $limit
            WITH startNode(r) AS a, r, endNode(r) AS b
            RETURN labels(a)[0] AS a_label, coalesce(a.name, a.id) AS a_name, type(r) AS rel,
                   properties(r) AS props, labels(b)[0] AS b_label, coalesce(b.name, b.id) AS b_name
            """,
            ids=seed_ids, skip=list(skip_labels), limit=limit,
        )
        facts = []
        for e in edges:
            props = ", ".join(f"{k}: {v}" for k, v in e["props"].items() if v)
            facts.append(f"({e['a_label']}: {e['a_name']}) -[{e['rel']}{' {' + props + '}' if props else ''}]-> "
                         f"({e['b_label']}: {e['b_name']})")
        return seed_ids, facts

    # ---------------------------------------------------------------- HINT — suggested ontology: writes

    def suggested_constraints(self) -> None:
        for label, key in [("Article", "id"), ("Clause", "id"), ("Crime", "name"), ("Case", "name"),
                           ("Substance", "name"), ("Person", "name"), ("Location", "name")]:
            self.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE")

    def add_law_article(self, article: dict) -> None:
        self.run(
            """
            MERGE (a:Article {id: $id}) SET a.title = $title, a.law = $law, a.doc_id = $doc_id
            FOREACH (crime IN CASE WHEN $crime IS NULL THEN [] ELSE [$crime] END |
                MERGE (c:Crime {name: crime}) MERGE (a)-[:DEFINES]->(c))
            WITH a
            UNWIND $clauses AS clause
            MERGE (cl:Clause {id: clause.id})
              SET cl.number = clause.number, cl.penalty = clause.penalty, cl.text = clause.text, cl.doc_id = $doc_id,
                  cl.min_years = clause.min_years, cl.max_years = clause.max_years, cl.life_or_death = clause.life_or_death
            MERGE (a)-[:HAS_CLAUSE]->(cl)
            FOREACH (s IN clause.substances | MERGE (sub:Substance {name: s}) MERGE (cl)-[:MENTIONS]->(sub))
            """,
            **article,
        )

    def add_news_case(self, case: dict, doc: Document) -> None:
        self.run(
            """
            MERGE (k:Case {name: $name})
              SET k.summary = $summary, k.date = $date, k.doc_id = $doc_id, k.source_title = $title
            FOREACH (loc IN CASE WHEN $location = '' THEN [] ELSE [$location] END |
                MERGE (l:Location {name: loc}) MERGE (k)-[:LOCATED_IN]->(l))
            FOREACH (crime IN $charges | MERGE (c:Crime {name: crime}) MERGE (k)-[:CHARGED_WITH]->(c))
            FOREACH (s IN $substances | MERGE (sub:Substance {name: s.name}) MERGE (k)-[r:INVOLVES]->(sub)
                SET r.amount = s.amount)
            FOREACH (p IN $people | MERGE (person:Person {name: p.name})
                SET person.aliases = coalesce(p.aliases, [])
                MERGE (person)-[r:INVOLVED_IN]->(k) SET r.role = p.role, r.charge = p.charge, r.sentence = p.sentence)
            """,
            name=case.get("name") or doc.metadata.get("title", doc.id),
            summary=case.get("summary", ""), date=case.get("date", ""), location=case.get("location", ""),
            charges=case.get("charges", []), people=[p for p in case.get("people", []) if p.get("name")],
            substances=[s for s in case.get("substances", []) if s.get("name")],
            doc_id=doc.id, title=doc.metadata.get("title", ""),
        )

    # ---------------------------------------------------------------- KG-3

    def context(self, question: str, doc_ids: list[str], max_facts: int = 60) -> list[str]:
        if ONTOLOGY == "hint":
            return self._context_hint(question, doc_ids, max_facts)
        return self._context_own(question, doc_ids, max_facts)

    def _context_own(self, question: str, doc_ids: list[str], max_facts: int = 60) -> list[str]:
        """Own ontology: the hint's facts, plus the highest-penalty clause ("khung cao nhất") of each article charged."""
        seed_ids, _ = self.seed_facts(question, doc_ids, limit=max_facts)
        cases = self.run(
            """
            MATCH (k:Case)
            WHERE elementId(k) IN $ids OR EXISTS { MATCH (s)--(k) WHERE elementId(s) IN $ids }
            RETURN elementId(k) AS id
            """,
            ids=seed_ids,
        )
        top = []
        if cases:
            rows = self.run(
                """
                MATCH (k:Case)-[:CHARGED_WITH]->(:Crime)<-[:DEFINES]-(a:Article)-[:HAS_CLAUSE]->(cl:Clause)
                WHERE elementId(k) IN $case_ids AND (cl.life_or_death OR cl.max_years IS NOT NULL)
                RETURN DISTINCT a.id AS article, a.title AS title, cl.number AS number, cl.text AS text,
                       cl.max_years AS max_years, cl.life_or_death AS life
                """,
                case_ids=[c["id"] for c in cases],
            )
            best: dict[str, tuple] = {}
            for row in rows:
                rank = (bool(row["life"]), row["max_years"] or 0)          # life/death first, then the longest term
                if row["article"] not in best or rank > best[row["article"]][0]:
                    best[row["article"]] = (rank, row)
            top = [self._clause_fact(row) for _, row in best.values()]
        facts = self._context_hint(question, doc_ids, max_facts)
        return list(dict.fromkeys(top + facts))[:max_facts]

    def _context_hint(self, question: str, doc_ids: list[str], max_facts: int = 60) -> list[str]:
        """Graph facts for a question: seeds + 1 hop, then the legal basis of every case reached (suggested ontology)."""
        # TODO KG-3: multi-hop retrieval over YOUR ontology.
        #   1. self.seed_facts(question, doc_ids) -> (seed_ids, facts)   (ontology-independent, already written)
        #   2. From the seeds, walk to the other KB through your bridge node (Cypher, see LAB_GUIDE Bước 5)
        #   3. Append one readable string per fact; return the list.
        #
        # HINT (suggested ontology):
        #   a. Cases that are a seed or next to one -> add f"Vụ việc '{name}': {summary}" to facts
        #        MATCH (k:Case) WHERE elementId(k) IN $ids OR EXISTS { MATCH (s)--(k) WHERE elementId(s) IN $ids }
        #   b. For those cases follow
        #        (Case)-[:CHARGED_WITH]->(Crime)<-[:DEFINES]-(Article)-[:HAS_CLAUSE]->(Clause)
        #      keep clause 1 + clauses that MENTION a Substance the case INVOLVES
        #   c. Articles named in the question ("Điều 251" -> re.findall(r"[Đđ]iều (\d+)", question)):
        #      clause 1 + clauses mentioning find_substances(question)
        #   d. One fact per clause: f"[{article_id} - {title}] khoản {number}: {text}"
        seed_ids, facts = self.seed_facts(question, doc_ids, limit=max_facts)

        # a. Cases that are a seed, or directly connected to one.
        cases = self.run(
            """
            MATCH (k:Case)
            WHERE elementId(k) IN $ids OR EXISTS { MATCH (s)--(k) WHERE elementId(s) IN $ids }
            RETURN elementId(k) AS id, k.name AS name, k.summary AS summary
            """,
            ids=seed_ids,
        )
        case_facts = [f"Vụ việc '{c['name']}': {c['summary']}" for c in cases if c["summary"]]

        # b. Legal basis of those cases: clause 1, plus clauses that mention a substance the case involves.
        law_facts = []
        if cases:
            law = self.run(
                """
                MATCH (k:Case)-[:CHARGED_WITH]->(:Crime)<-[:DEFINES]-(a:Article)-[:HAS_CLAUSE]->(cl:Clause)
                WHERE elementId(k) IN $case_ids
                  AND (cl.number = 1 OR EXISTS { (cl)-[:MENTIONS]->(:Substance)<-[:INVOLVES]-(k) })
                RETURN a.id AS article, a.title AS title, cl.number AS number, cl.text AS text
                ORDER BY article, number
                """,
                case_ids=[c["id"] for c in cases],
            )
            law_facts += [self._clause_fact(row) for row in law]

        # c. Articles named in the question ("Điều 251"): clause 1 + clauses about substances in the question.
        substances = find_substances(question)
        for number in re.findall(r"[Đđ]iều (\d+)", question):
            article = self.run(
                """
                MATCH (a:Article)-[:HAS_CLAUSE]->(cl:Clause)
                WHERE a.id STARTS WITH $prefix
                  AND (cl.number = 1 OR EXISTS { (cl)-[:MENTIONS]->(s:Substance) WHERE s.name IN $subs })
                RETURN a.id AS article, a.title AS title, cl.number AS number, cl.text AS text
                ORDER BY article, number
                """,
                prefix=f"Điều {number} ", subs=substances,
            )
            law_facts += [self._clause_fact(row) for row in article]

        # Case summaries and legal basis first (most useful), then the 1-hop edges from the seeds.
        return list(dict.fromkeys(case_facts + law_facts + facts))[:max_facts]

    @staticmethod
    def _clause_fact(row: dict) -> str:
        return f"[{row['article']} - {row['title']}] khoản {row['number']}: {row['text']}"

# ---------------------------------------------------------------------------------------------- KG-2

def build_graph(graph: Neo4jGraph, law_docs: list[Document], news_docs: list[Document],
                llm_fn: Callable[..., str]) -> None:
    """Load both KBs into an empty graph. llm_fn(prompt, json_mode=False) -> str (metered OpenAI chat)."""
    if ONTOLOGY == "hint":
        return _build_graph_hint(graph, law_docs, news_docs, llm_fn)
    graph.suggested_constraints()
    articles = [parse_law_article(doc) for doc in law_docs]           # regex: law text is regular
    for article in articles:
        graph.add_law_article(article)
    crimes = sorted({a["crime"] for a in articles if a["crime"]})     # the bridge vocabulary
    extract = lambda prompt: llm_fn(prompt, json_mode=True)
    for doc in news_docs:                                             # LLM: news prose
        for case in extract_news_cases(doc, extract, crimes):
            case["substances"] = canonical_substances(case.get("substances", []))
            graph.add_news_case(case, doc)

def canonical_substances(items: list[dict]) -> list[dict]:
    """One entry per canonical substance (own ontology): 'ketamine' and 'Ketamine' become one node."""
    merged: dict[str, dict] = {}
    for item in items:
        name = canonical_substance(item.get("name") or "")
        if name and name not in merged:
            merged[name] = {**item, "name": name}
    return list(merged.values())

def _build_graph_hint(graph: Neo4jGraph, law_docs: list[Document], news_docs: list[Document],
                      llm_fn: Callable[..., str]) -> None:
    """The suggested ontology, unchanged (KG_ONTOLOGY=hint)."""
    # TODO KG-2: create YOUR ontology in Neo4j from both KBs.
    #   Contract: every node created from one document has the property doc_id = Document.id.
    #   Fastest start: the HINT helpers above (parse_law_article, extract_news_cases, suggested_constraints,
    #   add_law_article, add_news_case). Own ontology + report/ONTOLOGY.md = bonus (SUBMISSION.md).
    graph.suggested_constraints()
    articles = [parse_law_article(doc) for doc in law_docs]           # regex: law text is regular
    for article in articles:
        graph.add_law_article(article)
    crimes = sorted({a["crime"] for a in articles if a["crime"]})     # the bridge vocabulary
    extract = lambda prompt: llm_fn(prompt, json_mode=True)
    for doc in news_docs:                                             # LLM: news prose
        for case in extract_news_cases(doc, extract, crimes):
            graph.add_news_case(case, doc)

# ---------------------------------------------------------------------------------------------- KG-4

GRAPH_PROMPT = """Trả lời câu hỏi chỉ dựa trên ngữ cảnh (đoạn văn bản và dữ kiện từ knowledge graph).
Nêu rõ số Điều luật khi có. Nếu ngữ cảnh không đủ, nói không đủ thông tin.

Dữ kiện knowledge graph:
{facts}

Đoạn văn bản:
{chunks}

Câu hỏi: {question}
Trả lời:"""

class GraphRAGAgent:
    """Hybrid GraphRAG: the same vector top-k as flat RAG, plus facts expanded from the graph."""

    def __init__(self, store: EmbeddingStore, graph: Neo4jGraph, llm_fn: Callable[[str], str]) -> None:
        self.store = store
        self.graph = graph
        self.llm_fn = llm_fn

    def answer(self, question: str, top_k: int = 3) -> str:
        chunks = self.store.search(question, top_k=top_k)             # same retrieval as flat RAG
        doc_ids = list(dict.fromkeys(chunk["metadata"]["doc_id"] for chunk in chunks))
        facts = self.graph.context(question, doc_ids)
        prompt = GRAPH_PROMPT.format(
            facts="\n".join(f"- {fact}" for fact in facts) or "(không có)",
            chunks="\n\n".join(f"[{i}] {chunk['content']}" for i, chunk in enumerate(chunks, start=1)),
            question=question,
        )
        return self.llm_fn(prompt)
