# KNHANES RAG Assistant

질병관리청 국민건강영양조사(KNHANES) 원시자료 이용지침서를 기반으로 기수별 정보를 검색·비교할 수 있도록 구현한 RAG(Retrieval-Augmented Generation) 기반 분석 어시스턴트입니다.

## 프로젝트 소개

국민건강영양조사 이용지침서는 조사 기수마다 변수 구성과 코드 체계가 달라 동일한 항목을 여러 기수에 걸쳐 비교하려면 각 지침서를 반복적으로 확인해야 합니다. 본 프로젝트는 제1기~제9기 이용지침서를 벡터 데이터베이스로 구축하고, 사용자의 질문에 따라 필요한 기수의 문서를 검색하여 기수별 차이를 비교할 수 있도록 설계한 RAG 시스템입니다.

단순 벡터 유사도 검색만 사용하는 대신 **기수별 검색 범위 제어**, **Vector Search + Keyword Force Retrieval 기반 Hybrid Search**, **LangChain Tool Calling**, **페이지 단위 출처 메타데이터**를 결합하였습니다.

## 주요 기능

- **Dynamic Scope**
  - 사용자가 선택한 기수 또는 질문에 포함된 기수를 검색 범위로 사용
  - 별도 범위가 없으면 제1기~제9기 전체 문서를 대상으로 검색

- **Batch-by-Batch Retrieval**
  - 각 기수별로 독립적으로 검색하여 특정 기수의 결과가 다른 기수 결과에 묻히지 않도록 구성

- **Hybrid Search**
  - Chroma 기반 Vector Search
  - 질의에서 변수명·영문 키워드를 추출한 Keyword Force Retrieval
  - 한국어 표현을 주요 변수명으로 확장하는 간단한 Query Expansion

- **Tool Calling**
  - LLM이 질문에서 기수·연도 조건을 해석하고 `search_documents` 도구를 호출
  - 검색 결과를 바탕으로 최종 답변 생성

- **Source-aware Response**
  - 문서명, 기수, 페이지 정보를 메타데이터로 관리
  - 검색 결과의 출처를 최종 응답과 함께 반환

- **Web Interface**
  - FastAPI 기반 서버
  - WebSocket 기반 실시간 질의응답
  - 기수별 검색 범위 선택 UI

## 시스템 흐름

```text
KNHANES PDF Guidelines
        │
        ▼
PDF Text Extraction
        │
        ▼
Metadata Parsing
(batch / year / page)
        │
        ▼
Recursive Text Splitting
(chunk_size=800, overlap=300)
        │
        ▼
OpenAI Embeddings
        │
        ▼
Chroma Vector Database
        │
        ▼
User Question
        │
        ├── Batch / Year Scope
        ├── Vector Search
        └── Keyword Force Retrieval
        │
        ▼
Context Aggregation
        │
        ▼
LLM Tool Calling & Answer Generation
        │
        ▼
Answer + Sources
```

## 데이터 처리

- 대상 문서: 국민건강영양조사 제1기~제9기 원시자료 이용지침서
- PDF 처리: `pypdf`
- 텍스트 분할: `RecursiveCharacterTextSplitter`
- Chunk size: 800
- Chunk overlap: 300
- Embedding model: `text-embedding-3-small`
- Vector DB: Chroma
- LLM: `gpt-4o`

원본 PDF 파일은 저장소에 포함하지 않습니다. 실행 시 `data/` 폴더에 이용지침서 PDF를 준비한 뒤 `ingest.py`를 실행하면 됩니다.

## Repository Structure

```text
.
├── README.md
├── app.py
├── ingest.py
├── requirements.txt
├── .gitignore
├── data/
│   └── README.md
├── templates/
│   ├── chat.html
│   └── landing.html
└── static/
    ├── main.js
    └── style.css
```

## 실행 방법

### 1. 환경 설정

```bash
pip install -r requirements.txt
```

프로젝트 루트에 `.env` 파일을 생성하고 OpenAI API Key를 설정합니다.

```env
OPENAI_API_KEY=YOUR_OPENAI_API_KEY
```

### 2. 지침서 적재

```bash
python ingest.py
```

기존 Chroma DB를 삭제하고 다시 생성하려면 다음과 같이 실행합니다.

```bash
python ingest.py --force
```

### 3. 서버 실행

```bash
uvicorn app:app --reload
```

실행 후 `http://127.0.0.1:8000`에서 웹 인터페이스를 확인할 수 있습니다.

## 사용 기술

- Python
- FastAPI / WebSocket
- LangChain
- OpenAI API
- ChromaDB
- PyPDF
- HTML / CSS / JavaScript

## 프로젝트 정보

- 수행 형태: 캡스톤디자인 팀 프로젝트
- 역할: 팀 멤버
- 주요 기여: RAG 검색 구조 설계, 기수별 검색 범위 제어, Hybrid Retrieval 및 웹 기반 질의응답 시스템 구현
