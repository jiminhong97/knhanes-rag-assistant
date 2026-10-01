import asyncio
import json
import os
import sys
import re # 정규표현식 라이브러리 추가
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document

# --- 1. 초기 설정 ---
load_dotenv()

# 주요 경로 및 설정 정의
CHROMA_DB_PATH = "chroma_db"
COLLECTION_NAME = "health_guide_collection"
EMBEDDING_MODEL = "text-embedding-3-small"
LLM_MODEL = "gpt-4o"


from typing import List, Optional
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate


# ... existing imports ...

# --- 데이터 모델 정의 ---
class SearchFilter(BaseModel):
    batches: Optional[List[int]] = Field(default=None, description="검색할 기수(Batch) 목록 (예: [8, 9])")
    years: Optional[List[int]] = Field(default=None, description="검색할 연도 목록 (예: [2022, 2023])")

# --- 챗봇 클래스 리팩토링 ---
class VectorSearchChatbot:
    """
    Tool Calling 및 다중 필터링을 지원하는 챗봇
    """
    def __init__(self):
        if not os.path.exists(CHROMA_DB_PATH):
            raise FileNotFoundError(f"데이터베이스 경로 '{CHROMA_DB_PATH}'를 찾을 수 없습니다.")
            
        self.llm = ChatOpenAI(model_name=LLM_MODEL, temperature=0)
        self.embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL)
        self.vectorstore = Chroma(
            persist_directory=CHROMA_DB_PATH,
            embedding_function=self.embeddings,
            collection_name=COLLECTION_NAME,
        )
        
        # 가용 메타데이터 분석
        self.available_batches = self._get_batch_details()
        self.current_filters = {"batches": []} # 사용자가 설정한 수동 필터
        self.latest_search_sources = [] # 툴 호출 시 출처 정보를 저장할 임시 저장소
        
        # 툴 바인딩
        self._bind_tools()

    def _get_batch_details(self) -> List[dict]:
        """DB에서 기수별 연도 정보를 추출합니다."""
        all_metadatas = self.vectorstore.get(include=["metadatas"])['metadatas']
        batch_info = {}
        
        for m in all_metadatas:
            if 'batch' in m and 'year_start' in m:
                b = m['batch']
                y_start = m['year_start']
                y_end = m.get('year_end', y_start)
                
                if b not in batch_info:
                    batch_info[b] = {"start": y_start, "end": y_end}
                else:
                    batch_info[b]["start"] = min(batch_info[b]["start"], y_start)
                    batch_info[b]["end"] = max(batch_info[b]["end"], y_end)
        
        result = []
        for b in sorted(batch_info.keys()):
            years_str = f"{batch_info[b]['start']}"
            if batch_info[b]['start'] != batch_info[b]['end']:
                years_str += f"-{batch_info[b]['end']}"
            result.append({"batch": b, "years": years_str})
            
        return result

    def _bind_tools(self):
        """LLM에 사용할 도구를 정의하고 바인딩합니다."""
        
        @tool("search_documents")
        def search_documents(query: str, batches: Optional[List[int]] = None, years: Optional[List[int]] = None) -> str:
            """
            국민건강영양조사 지침서 문서에서 정보를 검색합니다.
            특정 기수(batches)나 연도(years)를 지정하여 검색 범위를 좁힐 수 있습니다.
            기수를 지정하지 않으면 모든 기수(1기~9기)를 전수 검색하여 비교 분석할 수 있는 자료를 제공합니다.
            """
            # 1. 대상 기수 선정 (Target Selection)
            # 수동 필터 우선 적용
            manual_batches = set(self.current_filters["batches"]) if self.current_filters["batches"] else set()
            
            target_batches = []
            
            if batches:
                # LLM이 기수를 지정한 경우
                if manual_batches:
                    # 교집합 계산
                    intersection = manual_batches.intersection(set(batches))
                    target_batches = sorted(list(intersection))
                    if not target_batches:
                        return "설정된 기수 필터와 요청된 기수가 일치하지 않아 검색 결과가 없습니다."
                else:
                    target_batches = sorted(batches)
            else:
                # LLM이 기수를 지정하지 않은 경우
                if manual_batches:
                    target_batches = sorted(list(manual_batches))
                else:
                    # 필터가 없으면 1기~9기 전수 검색 (데이터에 존재하는 기수만)
                    target_batches = [b['batch'] for b in self.available_batches]

            print(f"[Tool] 검색 쿼리: '{query}', 대상 기수: {target_batches}")

            # 2. Hybrid Search (Vector + Keyword Force Retrieval)
            # A. Query Expansion (한국어 -> 영어 변수명 매핑)
            KEYWORD_MAP = {
                '지역': 'region',
                '체중': 'HE_WT', 
                '몸무게': 'HE_WT',
                '신장': 'HE_HT',
                '키': 'HE_HT',
                '성별': 'sex',
                '나이': 'age',
                '연령': 'age',
                '흡연': 'BS3_1', # 일반적인 흡연 여부 변수
                '음주': 'BD1_11' # 일반적인 음주 빈도 변수
            }
            
            # 영어 변수명(키워드) 추출 - 예: 'region', 'age'
            extracted_keywords = re.findall(r'[a-zA-Z0-9_]{2,}', query)
            
            # 한국어 매핑 키워드 추가
            mapped_keywords = []
            for kor_key, eng_val in KEYWORD_MAP.items():
                if kor_key in query:
                    mapped_keywords.append(eng_val)
                    
            # 최종 키워드 리스트 (영어 추출 + 한국어 매핑)
            all_keywords = extracted_keywords + mapped_keywords
            unique_keywords = list(set([k for k in all_keywords if k.lower() not in ['code', 'variable', 'search', 'query']]))
            
            if unique_keywords:
                print(f"[Tool] 감지된 중요 키워드(확장 포함): {unique_keywords}")

            all_docs = []
            
            for batch in target_batches:
                batch_filter = {"batch": batch}
                batch_docs = []
                
                # B. Vector Search (유사도 검색)
                # 검색 실행 (기수 당 상위 3개 - 정밀도 향상 > K=6은 노이즈 많음)
                retriever = self.vectorstore.as_retriever(
                    search_kwargs={"k": 3, "filter": batch_filter}
                )
                vector_docs = retriever.invoke(query)
                batch_docs.extend(vector_docs)
                
                # C. Keyword Force Retrieval (키워드 강제 검색)
                # 키워드가 포함된 문서는 유사도 점수와 상관없이 가져옴
                if unique_keywords:
                    for kw in unique_keywords:
                        try:
                            # Chroma $contains 연산자로 포함 여부 확인
                            # 주의: 대량 검색 방지를 위해 limit=3 설정
                            keyword_results = self.vectorstore.get(
                                where={"batch": batch},
                                where_document={"$contains": kw},
                                limit=3,
                                include=["metadatas", "documents"]
                            )
                            
                            if keyword_results['ids']:
                                print(f"[Tool] '{kw}' 포함 문서 강제 로드 (기수 {batch}): {len(keyword_results['ids'])}개")
                                for i, doc_id in enumerate(keyword_results['ids']):
                                    # Document 객체로 변환
                                    doc = Document(
                                        page_content=keyword_results['documents'][i],
                                        metadata=keyword_results['metadatas'][i]
                                    )
                                    batch_docs.append(doc)
                        except Exception as e:
                            print(f"[Tool] 키워드 검색 중 오류 (기수 {batch}, 키워드 {kw}): {e}")

                # 중복 제거 (Vector Search와 Keyword Search 결과가 겹칠 수 있음)
                seen_ids = set()
                unique_batch_docs = []
                for doc in batch_docs:
                    # 고유 ID 생성 (source + page + content hash fragments)
                    doc_id = f"{doc.metadata.get('source')}_{doc.metadata.get('page')}_{hash(doc.page_content[:20])}"
                    if doc_id not in seen_ids:
                        seen_ids.add(doc_id)
                        doc.metadata['result_batch'] = batch
                        unique_batch_docs.append(doc)
                
                all_docs.extend(unique_batch_docs)
            
            if not all_docs:
                return "검색 결과가 없습니다."
            
            # 3. 결과 포맷팅 (Context Aggregation)
            # 중복 제거 (혹시 모를 중복 방지)
            unique_docs = {}
            for doc in all_docs:
                doc_id = f"{doc.metadata.get('source')}_{doc.metadata.get('page')}"
                if doc_id not in unique_docs:
                    unique_docs[doc_id] = doc

            result = ""
            # 기수별로 정렬하여 출력
            sorted_docs = sorted(unique_docs.values(), key=lambda x: x.metadata.get('result_batch', 0))
            
            # 검색된 기수 집합 확인
            found_batches = set()

           # 그룹화된 출력 생성
            current_batch = -1
            for doc in sorted_docs:
                batch = doc.metadata.get('result_batch', 0)
                if isinstance(batch, int):
                    found_batches.add(batch)
                source = doc.metadata.get('source', '알 수 없음')
                page = doc.metadata.get('page', '?')
                
                if batch != current_batch:
                    result += f"\n[[제{batch}기 Context]]\n"
                    current_batch = batch

                # 출처 정보 저장 (ask 메서드에서 최종 반환 시 사용)
                self.latest_search_sources.append({"source": source, "page": page, "batch": batch})
                
                result += f"- {doc.page_content.strip()}\n  (출처: {source} p.{page})\n"
            
            # 검색되지 않은 기수 확인 및 메시지 추가
            missing_batches = sorted(list(set(target_batches) - found_batches))
            if missing_batches:
                missing_str = ", ".join([f"제{b}기" for b in missing_batches])
                result += f"\n[[참고]]\n다음 기수 문서에서는 검색어와 관련된 내용을 찾지 못했습니다: {missing_str}\n"
            
            return result

        self.tools = [search_documents]
        self.llm_with_tools = self.llm.bind_tools(self.tools)
        self.tool_map = {t.name: t for t in self.tools}

    def set_filters(self, batches: List[int]):
        """사용자 수동 필터를 설정합니다."""
        self.current_filters["batches"] = batches
        filter_status = f"{batches}기" if batches else "모든 기수"
        return {"status": "success", "message": f"검색 대상이 {filter_status} 문서로 설정되었습니다."}

    def ask(self, question: str):
        """
        사용자 질문 -> LLM (Tool Call 결정) -> Tool 실행 -> 최종 답변
        """
        # 검색 출처 초기화
        self.latest_search_sources = []
        
        # 현재 검색 범위 파악
        if self.current_filters["batches"]:
            scope_str = f"{self.current_filters['batches']}기"
        else:
            scope_str = "제1기 ~ 제9기 (전체 기간)"

        # --- History-Aware Comparison System Prompt ---
        system_prompt = (
            "당신은 국민건강영양조사(KNHANES)의 모든 기수별 데이터와 변경 이력을 꿰뚫고 있는 수석 데이터 분석가입니다.\n"
            f"현재 분석 대상 범위는 **{scope_str}** 입니다. 이 범위 내에서 답변하세요.\n\n"
            "### Strict Response Rules (답변 작성 2대 절대 원칙)\n"
            "**Rule 1: Mandatory Coverage (선택된 기수 전수 보고)**\n"
            f"- 사용자가 선택했거나 검색 범위(**{scope_str}**)에 포함된 **모든 기수**는 반드시 답변에 포함되어야 합니다.\n"
            "- '일부 기수는 생략합니다' 또는 암묵적으로 기수를 건너뛰는 행위는 **절대 금지**입니다.\n"
            "- 데이터가 없는 기수가 있다면 '제N기는 데이터가 확인되지 않습니다'라고 명시적으로 언급하세요.\n\n"
            "**Rule 2: Timeline Compression (타임라인 압축 알고리즘 - 최우선 순위)**\n"
            "- 답변 작성 전, 반드시 **'타임라인 압축'** 과정을 거치세요. 절대 기수 순서대로 그냥 나열하지 마세요.\n"
            "- **알고리즘**:\n"
            "  1. 모든 기수(1~9기)의 정보를 먼저 머릿속으로 수집합니다.\n"
            "  2. **인접한 기수끼리 비교**합니다. (예: 2기 내용 == 3기 내용?)\n"
            "  3. 내용(변수명, 코드, 개수, 단위)이 완벽히 같다면 **무조건 병합**합니다.\n"
            "  4. **달라지는 지점(Breakpoint)**에서만 줄을 바꿉니다.\n"
            "- **Exmaple (지역변수 Case)**:\n"
            "  - Fact: 1기(15개), 2기(16개), 3기(16개), ..., 6기(16개), 7기(17개)...\n"
            "  - Bad Output: '2기는 16개입니다. 3~6기도 16개입니다.' (덜 합쳐짐 - 절대 금지)\n"
            "  - Good Output: '**제2기~제6기**: 모두 16개 시도로 구분됩니다.' (완벽한 압축)\n\n"
            "**Rule 3: Context-Aware Variable Introduction (상황별 변수명 표기)**\n"
            "- **Case A (특정 기수 질문)**: 사용자가 특정 기수를 콕 집어 물어봤다면, 답변 첫 줄에 **'변수명: `이름`'**을 명시하세요.\n"
            "  - 예: '3기 체중?' -> **변수명**: `HE_WT` (제3기)\n"
            "- **Case B (전체/기간 질문 - 변수명 동일)**: 변수명이 바뀌지 않았다면, 첫 줄에 **'변수명: `이름` (전 기간 동일)'**을 명시하세요.\n"
            "- **Case C (전체/기간 질문 - 변수명 변경)**: 기수별로 변수명이 다르다면, 절대 첫 줄에 하나로 요약하지 마세요. 대신 **서술형으로 풀어서 설명**하세요.\n"
            "  - 예: '이 변수는 초기에는 `HE_WT`였으나, 4기부터는 `NEW_WT`로 변경되었습니다.'\n\n"
            "### Tool Calling Strategy (도구 사용 전략)\n"
            "1. **Argument Extraction**: 사용자의 질문에 특정 기수('3기', '제3기')나 연도('2005년')가 명시되어 있다면, 반드시 **`search_documents` 도구의 `batches` 또는 `years` 인자로 추출**하여 전달하세요.\n"
            "   - 예: '3기 지역구분 변수' -> `search_documents(query='지역구분 변수', batches=[3])`\n"
            "   - 예: '2010년 흡연율' -> `search_documents(query='흡연율', years=[2010])`\n"
            "2. **Query Cleaning**: 도구의 `query` 인자에는 기수나 연도 같은 필터링 용어를 제외하고, **검색할 핵심 키워드**만 남기는 것이 좋습니다.\n\n"
            "### 답변 작성 원칙\n"
            "1. **search_documents 도구 사용 필수**: 반드시 도구를 사용하여 근거를 찾으세요. 내 지식으로 답하지 마세요.\n"
            "2. **명확한 구분**: 기수별 차이가 있다면 두루뭉술하게 설명하지 말고 정확히 집어내세요.\n"
            "3. **본문 내 출처 표기 절대 금지 (Clean Text)**: \n"
            "   - **Rule**: 본문에는 오직 설명 텍스트만 작성하세요. 문장 끝이나 중간에 `[제1기 문서]`, `(p.16)` 같은 출처 표기를 절대 넣지 마세요.\n"
            "   - Bad: '15개 시도로 구분됩니다 [제1기 문서] (p.16).'\n"
            "   - Good: '15개 시도로 구분됩니다.' (깔끔한 문장)\n"
            "4. **SOURCES 태그 (엄격 적용)**: 답변의 맨 마지막에 반드시 **'SOURCES:'** 태그를 붙이세요.\n"
            "   - **규칙 1 (전수 표기)**: 1~9기를 묶어서 답했다면, **1기부터 9기까지 모든 출처**를 다 써야 합니다.\n"
            "   - **규칙 2 (페이지 필수)**: `(p.페이지)`가 없으면 틀린 답변으로 간주합니다. 반드시 메타데이터를 확인해서 적으세요. 없으면 `(p.확인불가)`.\n"
            "   - 형식: `SOURCES: [제1기 문서명] (p.16), [제2기 문서명] (p.20), ...`"
        )

        messages = [SystemMessage(content=system_prompt), HumanMessage(content=question)]
        
        # 1. LLM 호출 (Tool Call 판별)
        ai_msg = self.llm_with_tools.invoke(messages)
        messages.append(ai_msg)
        
        # 2. Tool Call 처리
        if ai_msg.tool_calls:
            for tool_call in ai_msg.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]
                
                print(f"[Agent] 도구 호출: {tool_name}({tool_args})")
                
                if tool_name in self.tool_map:
                    tool_func = self.tool_map[tool_name]
                    tool_output = tool_func.invoke(tool_args)
                    messages.append(ToolMessage(tool_call_id=tool_call["id"], content=str(tool_output)))
            
            # 3. 도구 결과 포함하여 최종 답변 생성
            final_response = self.llm.invoke(messages)
            
            # 4. 응답 파싱 (SOURCES: 태그 분리)
            content = final_response.content
            if "SOURCES:" in content:
                parts = content.split("SOURCES:")
                answer_text = parts[0].strip()
                source_text = parts[1].strip()
            else:
                answer_text = content
                # SOURCES 태그가 없으면 자동 생성
                if self.latest_search_sources:
                    unique_sources = {}
                    for s in self.latest_search_sources:
                        batch_prefix = f"[제{s.get('batch', '?')}기] " if s.get('batch') else ""
                        key = f"{batch_prefix}{s['source']} (p.{s['page']})"
                        unique_sources[key] = True
                    source_text = ", ".join(unique_sources.keys())
                else:
                    source_text = ""

            return {"answer": answer_text, "sources": source_text}
        
        # 도구 호출이 없는 경우 (직접 답변 시도)
        return {"answer": ai_msg.content, "sources": ""}


app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

try:
    chatbot = VectorSearchChatbot()
except FileNotFoundError as e:
    print(f"초기화 오류: {e}", file=sys.stderr)
    chatbot = None

@app.get("/", response_class=HTMLResponse)
async def read_landing(request: Request):
    return templates.TemplateResponse("landing.html", {"request": request})

@app.get("/chat", response_class=HTMLResponse)
async def read_chat(request: Request):
    return templates.TemplateResponse("chat.html", {"request": request})

@app.get("/api/filters")
async def get_filters():
    if chatbot:
        return {"batches": chatbot.available_batches}
    raise HTTPException(status_code=503, detail="챗봇 서비스 초기화 실패")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    if not chatbot:
        await websocket.send_json({
            "type": "error",
            "data": "챗봇을 초기화할 수 없습니다. 'ingest.py'를 실행했는지 확인하세요."
        })
        await websocket.close()
        return

    try:
        await websocket.send_json({"type": "system", "data": "챗봇이 준비되었습니다. 질문을 입력해주세요."})

        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            message_type = message_data.get("type")
            message_content = message_data.get("content")

            if message_type == "question":
                # 비동기 실행을 위해 run_in_executor 사용
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, chatbot.ask, message_content)
                await websocket.send_json({"type": "answer", "data": result})

            elif message_type == "filter_change":
                # content는 [8, 9] 형태의 리스트여야 함
                response = chatbot.set_filters(message_content)
                await websocket.send_json({"type": "system", "data": response["message"]})

    except WebSocketDisconnect:
        print("클라이언트 연결이 끊어졌습니다.")
    except Exception as e:
        print(f"웹소켓 오류 발생: {e}")
        await websocket.send_json({"type": "error", "data": f"오류가 발생했습니다: {e}"})
    finally:
        if not websocket.client_state.DISCONNECTED:
            await websocket.close()
