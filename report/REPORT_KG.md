# Báo cáo Day 19 — Flat RAG vs GraphRAG

**Họ tên:** Nguyễn Đức Thịnh  **MSSV:** 2A202602468  **Ngày:** 2026-10-05

> Kỳ vọng và thang điểm: `SUBMISSION.md`. Mọi số liệu phải khớp với `ket_qua_benchmark_kg.txt` (ontology riêng, file chính) và `ket_qua_benchmark_kg.hint.txt` (ontology gợi ý, đối chứng cho bonus). Bản thiết kế ontology nộp riêng ở `report/ONTOLOGY.md`.

## 1. Chi phí (10 điểm)

Dán 2 bảng `Indexing` và `Querying` từ `ket_qua_benchmark_kg.txt`:

```
Chat model: openai:gpt-4o-mini | Embedding: openai:text-embedding-3-small | top_k=3 | chunk_size=800 | chunks=176 | KG: 200 nodes / 380 rels

== Indexing (one-off)
pipeline  calls    in_tok  out_tok       USD  seconds
flat        176     56072        0   0.00112     43.8
graph       196     91958     4892   0.00944    101.9

== Querying (mean per question)
pipeline  recall  judge   in_tok  out_tok       USD  seconds
flat        0.43   1.00      694       48   0.00013     1.36
graph       0.94   1.83     5067       93   0.00081     1.72
```

| Chỉ số | Flat | Graph | Graph / Flat |
| --- | --- | --- | --- |
| Indexing USD | 0,00112 | 0,00944 | ×8,4 |
| Indexing giây | 43,8 | 101,9 | ×2,3 |
| Mỗi câu: USD | 0,00013 | 0,00081 | ×6,2 |
| Mỗi câu: giây | 1,36 | 1,72 | ×1,3 |
| Mỗi câu: in_tok | 694 | 5067 | ×7,3 |

Dòng `graph` trong `Indexing` đã bao gồm chi phí embed của Flat (`graph_index = flat_index + kg_build`). Chi phí riêng của việc dựng KG: 20 lần gọi LLM, 35.886 token đầu vào, 4.892 token đầu ra, 0,00832 USD, 58,1 giây.

**Chi phí tăng thêm đến từ đâu?** Hai nguồn. Thứ nhất, dựng KG cần 20 lần gọi LLM trích xuất trên 20 bài báo (luật được regex, không tốn token). Thứ hai, mỗi câu hỏi GraphRAG gửi thêm dữ kiện từ graph, khiến prompt dài gấp khoảng 7 lần (5.067 so với 694 token). Hòa vốn: chi phí dựng KG 0,00832 USD được bù bởi chênh lệch 0,00068 USD mỗi câu, tức khoảng **12 câu hỏi**. Với 6 câu benchmark, KG chưa hòa vốn về chi phí; với hệ thống trả lời hàng nghìn câu, chi phí dựng được chia đều.

**Đối chứng với ontology gợi ý** (`ket_qua_benchmark_kg.hint.txt`, cùng code và dữ liệu, một lần chạy riêng): graph recall 0,78, judge 1,67, 4.573 token đầu vào mỗi câu, 0,00072 USD mỗi câu, 205 node / 384 cạnh. Ontology riêng tốn thêm khoảng 11% token mỗi câu để đổi lấy Q4 đúng (xem mục 2 và `report/ONTOLOGY.md` mục 7).

## 2. Từng câu hỏi (10 điểm)

Bảng dùng `ket_qua_benchmark_kg.txt` (ontology riêng). Cột "Gợi ý" là kết quả graph của ontology gợi ý (`ket_qua_benchmark_kg.hint.txt`), để so sánh.

| Câu | Loại | Flat recall / judge | Graph recall / judge | Thắng | Gợi ý (graph recall / judge) | Vì sao (1 câu) |
| --- | --- | --- | --- | --- | --- | --- |
| Q1 | single-hop-law | 1,00 / 2 | 1,00 / 2 | Hòa | 1,00 / 2 | Đáp án nằm gọn trong một đoạn luật, vector search đã đủ |
| Q2 | single-hop-news | 1,00 / 2 | 1,00 / 2 | Hòa | 1,00 / 2 | Tên bị cáo và mức án có trong chunk tin; graph chỉ thêm Điều 251 |
| Q3 | cross-kb | 0,00 / 0 | 1,00 / 2 | **Graph** | 1,00 / 2 | Flat trả "Không đủ thông tin", có thể vì tên bị cáo và Điều 251 không nằm trong cùng top-3 chunk (chưa kiểm chứng); graph nối Person → Case → Crime → Article |
| Q4 | cross-kb | 0,00 / 0 | 1,00 / 2 | **Graph** | 0,67 / 1 | Flat không tìm được Điều 255. Graph ontology riêng trả đúng "tù chung thân" nhờ khung cao nhất; ontology gợi ý trả "tối đa 7 năm" (xem E2) |
| Q5 | cross-kb-multi-hop | 0,60 / 1 | 1,00 / 2 | **Graph** | 1,00 / 2 | Flat gọi nhầm "khoản b)" và khung hình phạt sai; graph chọn đúng khoản 4 Điều 250 nhờ cạnh `INVOLVES` (MDMA) và `MENTIONS` |
| Q6 | aggregation | 0,00 / 1 | 0,67 / 1 | Graph (recall), hòa (judge) | 0,00 / 1 | Cả hai đều trả lời đúng hướng là có MDMA; graph nêu được nhiều vụ hơn nhưng liệt kê Cái Quang Huy hai lần (E3). Lần chạy trước graph cho 0 |

Tổng: graph thắng rõ 3 câu (Q3, Q4, Q5) và thắng về recall ở Q6; hòa Q1, Q2. Không câu nào Flat thắng. Graph recall trung bình 0,94 so với 0,43 của Flat.

**Quy luật:** GraphRAG thắng khi đáp án cần nối hai KB qua một thực thể (tên người → tội → Điều). Flat đủ khi đáp án nằm trong một KB (Q1, Q2). Câu tổng hợp (Q6) được cải thiện một phần: graph mở rộng từ top-k chunk nên vẫn bỏ sót vụ không có chunk được tìm ra.

## 3. Phân tích lỗi (20 điểm)

Hai lỗi dưới đây được phát hiện trên ontology gợi ý (`ket_qua_benchmark_kg.hint.txt`). Ontology riêng đã sửa một phần cả hai lỗi, và phần còn lại được ghi rõ.

### Lỗi E2: Thiếu ngữ cảnh luật — chọn sai khoản nên trả lời sai khung hình phạt tối đa

- **Hiện tượng:** Q4 hỏi "có thể bị phạt tù tối đa bao nhiêu" cho tội tổ chức sử dụng trái phép chất ma túy (Điều 255). Câu trả lời của GraphRAG (gợi ý): *"Hành vi này có thể bị phạt tù tối đa 7 năm theo Điều 255 Bộ luật Hình sự."* Đáp án chuẩn: tù 20 năm hoặc tù chung thân (khoản 4). Judge chấm 1, recall 0,67, và kết quả này lặp lại ở cả hai lần chạy gợi ý.
- **Bằng chứng:** Dữ kiện graph trả về cho Q4 (ontology gợi ý, `graph.context`) chỉ có khoản 1 của Điều 255:

```
[Điều 255 BLHS - Tội tổ chức sử dụng trái phép chất ma túy] khoản 1: 1. Người nào tổ chức sử dụng trái phép chất ma túy dưới bất kỳ hình thức nào, thì bị phạt tù từ 02 năm đến 07 năm.
```

Khoản 4 không được đưa vào. Truy vấn kiểm chứng:

```cypher
MATCH (a:Article {id:'Điều 255 BLHS'})-[:HAS_CLAUSE]->(cl)-[:MENTIONS]->(s) RETURN count(s) AS n
```
```
n = 0
```

Điều 255 không có chất ma túy nào được `MENTIONS`, nên quy tắc "khoản 1 + khoản nhắc chất của vụ" chỉ lấy khoản 1. Khoản 4 có khung chung thân nhưng không có cạnh nào dẫn tới vụ việc:

```cypher
MATCH (a:Article {id:'Điều 255 BLHS'})-[:HAS_CLAUSE]->(cl) RETURN cl.number AS n, cl.penalty AS p ORDER BY n
```
```
1 "phạt tù từ 02 năm đến 07 năm"
2 "phạt tù từ 07 năm đến 15 năm"
3 "phạt tù từ 15 năm đến 20 năm"
4 "phạt tù 20 năm hoặc tù chung thân"
5 "phạt tiền từ 50.000.000 đồng đến 500.000.000 đồng, ..."
```

- **Nguyên nhân:** Bước **prompt/context** (KG-3) và **thiết kế ontology**. Dữ liệu khung hình phạt có đủ trong graph dưới dạng chuỗi (`Clause.penalty`), nhưng không có thuộc tính số để truy vấn "khung cao nhất", và cách chọn khoản bỏ mất khoản 4.
- **Đã sửa trong ontology riêng:** thêm `min_years`, `max_years`, `life_or_death` trên `Clause`, và luôn đưa khoản khung cao nhất của mỗi Điều bị truy tố vào dữ kiện. Kết quả: Q4 (riêng) trả "tối đa 20 năm hoặc tù chung thân theo Điều 255 BLHS khoản 4", recall 1,00, judge 2, ở **cả hai** lần chạy. Cypher: `MATCH (a:Article {id:'Điều 255 BLHS'})-[:HAS_CLAUSE]->(cl) RETURN cl.number, cl.max_years, cl.life_or_death` cho khoản 4 = `20, true`, các khoản còn lại `life_or_death = false`.
- **Đánh đổi:** mỗi câu hỏi tốn thêm khoảng 500 token (tăng khoảng 11%).

### Lỗi E3: Trùng thực thể — cùng một vụ thành hai node, cùng một chất thành hai node

- **Hiện tượng:** (a) Vụ án của Cái Quang Huy xuất hiện thành hai node `Case` từ hai bài báo khác nhau. Node từ bài `news-100260918080821054` không có cạnh `CHARGED_WITH`, nên bị gãy khỏi node cầu nối `Crime`. (b) Chất ma túy trùng trên ontology gợi ý: `Ketamine`/`ketamine` và `Methamphetamine`/`methamphetamine`. Hệ quả: truy vấn Q6 "MDMA" trả về 5 vụ, trong đó có hai vụ cùng là Cái Quang Huy. (c) Trên ontology riêng, lỗi (b) đã được sửa, nhưng lỗi (a) vẫn còn: `kg_my_case.png` cho thấy `Case (2)` cho Cái Quang Huy, và câu trả lời Q6 của lần chạy riêng liệt kê "Vụ vận chuyển ma túy của Cái Quang Huy" và "Vụ vận chuyển ma túy từ Đức về Việt Nam" như hai vụ khác nhau, cùng nêu "9,6kg MDMA".
- **Bằng chứng:**

```cypher
MATCH (p:Person {name:'Cái Quang Huy'})-[r:INVOLVED_IN]->(k:Case)
OPTIONAL MATCH (k)-[:CHARGED_WITH]->(c:Crime)
OPTIONAL MATCH (k)-[i:INVOLVES]->(s:Substance)
RETURN k.name, k.doc_id, collect(DISTINCT c.name) AS crimes, collect(DISTINCT s.name + ' ' + coalesce(i.amount,'')) AS subs
```
```
"Vụ vận chuyển ma túy của Cái Quang Huy"  | news-100260918080821054 | []                                  | ["MDMA 9,6kg", "Ketamine 406g"]
"Vụ vận chuyển ma túy từ Đức về Việt Nam" | news-100260917203001265 | ["vận chuyển trái phép chất ma túy"] | ["MDMA 4.3kg", "Ketamine 406g"]
```

Hai node cùng mô tả một vụ nhưng khối lượng MDMA khác nhau (9,6kg so với 4,3kg), và chỉ một node nối được tới Điều 250. Danh sách `Substance` của ontology gợi ý:

```cypher
MATCH (s:Substance) RETURN s.name ORDER BY toLower(s.name)
```
```
Amphetamine; Cocaine; Heroine; Ketamine; MDMA; Methamphetamine; XLR-11; chất ma túy; côca; cần sa; etomidate; ketamine; ma túy; methamphetamine; thuốc lắc; thuốc phiện
```

- **Nguyên nhân:** Bước **trích xuất LLM** (`extract_news_cases`) và **khóa `MERGE`** (ontology). `Case` được khóa theo tên do LLM đặt, nên cùng một vụ với hai tên khác nhau không gộp được. `Substance` được khóa theo chuỗi thô do LLM trả về, không chuẩn hóa hoa thường, và từ chung chung như "ma túy" trở thành node.
- **Đã sửa một phần trong ontology riêng:** `canonical_substance` gộp tên chất và bỏ từ chung chung. Kết quả: 11 node `Substance`, không còn cặp trùng (tổng node 205 → 200). **Chưa sửa:** lỗi trùng `Case` của Cái Quang Huy vẫn còn (`Case (2)` trong `kg_my_case.png`).
- **Đề xuất sửa cho phần còn lại:** gộp `Case` theo `(người chính, tội)` hoặc qua một lần gọi LLM để nhận diện vụ đã có. Đánh đổi: thêm khoảng 0,001–0,002 USD mỗi bài khi dựng KG.

## 4. Kết luận (5 điểm)

**Khi nào nên dùng KG?** Trong benchmark này, khi đáp án cần nối hai KB qua một thực thể (người → vụ → tội → Điều luật). Flat RAG trả lời 0 trên 3 câu cross-kb (Q3, Q4 và chỉ đạt một phần Q5), còn GraphRAG (ontology riêng) đạt recall 1,00 ở Q3, Q4 và Q5. Q3 và Q4 đúng hoàn toàn về nội dung; Q5 đúng khoản và khung, nhưng cách diễn đạt còn thừa.

**Khi nào Flat RAG đủ?** Câu hỏi một KB, đáp án nằm gọn trong một đoạn: Q1 và Q2 cho cùng kết quả, Flat rẻ hơn khoảng 6 lần mỗi câu. Với hệ thống chủ yếu trả lời kiểu này, KG không đáng tiền.

**Số liệu đối chiếu:** dựng KG thêm 0,00832 USD và khoảng 58 giây; mỗi câu thêm 0,00068 USD. Hòa vốn ở khoảng 12 câu hỏi. Với 6 câu benchmark, KG chưa đáng tiền về chi phí, nhưng đáng về độ đúng trên các câu cross-kb.

**Điều kiện cụ thể:** KG đáng tiền khi (1) hai KB có một thực thể chung ổn định (ở đây là tội danh, có danh sách chuẩn 13 mục), (2) câu hỏi cần nối qua thực thể đó, và (3) số câu hỏi đủ lớn để chi phí dựng được chia đều. Lỗi E3 (trùng `Case`) vẫn còn nên cần sửa thêm trước khi tin hoàn toàn vào kết quả của các vụ có nhiều bài báo.

## 5. Tự kiểm (5 điểm)

```
$ pytest tests/ -q
................................................                         [100%]
48 passed in 0.06s

$ python bench_kg.py --check
[OK] Dữ liệu: 18 điều luật, 20 bài báo
[OK] KG-1 link_entity
[OK] Neo4j kết nối được
[provider] chat = openai:gpt-4o-mini | embedding = openai:text-embedding-3-small
[OK] KG-2 build_graph: 148 node / 292 cạnh, đường xuyên 2 KB dài 2 cạnh
[OK] KG-3 context: 17 dữ kiện, có Điều 251
[OK] KG-4 GraphRAGAgent.answer
[OK] Chi phí check: 1 lần gọi LLM, $0.00076. Graph nhỏ (luật + 1 bài) vẫn còn trong Neo4j để bạn xem; chạy --judge để dựng graph đầy đủ.
```

Lưu ý: `--check` và `--judge` đều xóa và dựng lại graph. `--check` được chạy sau khi đã chụp ảnh, nên Neo4j hiện chỉ còn graph nhỏ (luật + 1 bài). Ba ảnh được chụp từ đúng graph đầy đủ của lần `--judge` chính thức.

Ảnh Neo4j (đều từ graph 200 node / 380 cạnh):
- `report/img/kg_count.png` (Q-A): khung Results overview đếm theo label: Article 18, Case 15, Clause 99, Crime 13, Location 7, Person 37, Substance 11; và theo loại cạnh: CHARGED_WITH 19, DEFINES 13, HAS_CLAUSE 99, INVOLVED_IN 45, INVOLVES 21, LOCATED_IN 14, MENTIONS 169. Truy vấn dùng là `MATCH (n) OPTIONAL MATCH (n)-[r]->() RETURN n, r;` thay vì câu đếm Q-A nguyên văn, vì Results overview chỉ hiện khi kết quả là node và cạnh.
- `report/img/kg_cross_kb.png` (Q-B): `MATCH p=(:Person)-[:INVOLVED_IN]->(:Case)-[:CHARGED_WITH]->(:Crime)<-[:DEFINES]-(:Article) RETURN p LIMIT 25;`
- `report/img/kg_my_case.png` (Q-D): với người tự chọn là **Cái Quang Huy** (không phải Lê Minh Thành). Kết quả hiện `Case (2)`, cho thấy lỗi E3.

## Vấn đề gặp phải (không tính điểm)

- Docker Desktop chưa mở ban đầu; đã khởi động và tạo container `neo4j-drug-kg`.
- Khi điều khiển Chrome, một số thao tác bị hộp xác nhận quyền ("Claude in Chrome permission prompt") chặn và hết thời gian. Thử lại đúng thao tác đó thì chạy được; không có thao tác nào được bỏ qua quyền.
- Kết quả LLM không hoàn toàn tất định. Cùng ontology gợi ý, graph recall là 0,83 ở một lần và 0,78 ở lần khác (Q6 thay đổi 0,33 → 0). Với ontology riêng, graph recall là 0,83 và 0,94. Số liệu trong báo cáo lấy từ các file kết quả cuối cùng.
