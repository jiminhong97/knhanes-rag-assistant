import os
import shutil
import time
import argparse
import glob
import re
from dotenv import load_dotenv
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
from pypdf import PdfReader

# --- 1. 초기 설정 ---
load_dotenv()

# 주요 경로 및 설정 정의
DATA_DIR = "data"
CHROMA_DB_PATH = "chroma_db"
COLLECTION_NAME = "health_guide_collection"
EMBEDDING_MODEL = "text-embedding-3-small"

def extract_metadata_from_filename(filename):
    """파일 이름에서 메타데이터(기수, 연도 등)를 추출합니다."""
    basename = os.path.basename(filename)
    meta = {
        "source": basename, # 기본 소스는 파일명 전체
        "source_filename": basename
    }

    # 1. 깔끔한 소스 이름 추출 (기존 extract_source_name 로직 일부 차용 및 개선)
    clean_name = os.path.splitext(basename)[0]
    clean_name = re.sub(r"^국민건강영양조사[+\s]", "", clean_name)
    clean_name = re.sub(r"[+\s]원시자료[+\s]이용지침서.*$", "", clean_name)
    clean_name = re.sub(r"[+\s]원시자료[+\s]분석[+\s]지침서.*$", "", clean_name)
    clean_name = clean_name.replace('+', ' ').strip()
    
    if clean_name:
        meta["source"] = clean_name

    # 2. 기수(Batch) 추출
    # "제9기" -> 9
    batch_match = re.search(r'제(\d{1,2})기', basename)
    if batch_match:
        meta['batch'] = int(batch_match.group(1))

    # 3. 연도(Year) 추출
    # (1998), (2007-2009)
    year_match = re.search(r'\((\d{4})(?:-(\d{4}))?\)', basename)
    if year_match:
        start_year = int(year_match.group(1))
        # 끝 연도가 없으면 시작 연도와 동일하게 설정
        end_year = int(year_match.group(2)) if year_match.group(2) else start_year
        meta['year_start'] = start_year
        meta['year_end'] = end_year
    
    return meta


def ingest_data(force_reingest: bool = False):
    """
    지정된 디렉토리의 모든 PDF 문서를 로드, 분할하고 임베딩하여 ChromaDB에 저장합니다.
    --force 옵션이 주어지면 기존 DB를 삭제하고 새로 생성합니다.
    """
    if force_reingest and os.path.exists(CHROMA_DB_PATH):
        print(f"기존 '{CHROMA_DB_PATH}' 디렉토리를 삭제합니다.")
        shutil.rmtree(CHROMA_DB_PATH)

    if os.path.exists(CHROMA_DB_PATH):
        print(f"'{CHROMA_DB_PATH}' 디렉토리가 이미 존재합니다. 데이터 적재를 건너뜁니다.")
        print("새로 생성하려면 --force 옵션을 사용하여 스크립트를 실행하세요.")
        return

    print("데이터 적재를 시작합니다...")
    os.makedirs(CHROMA_DB_PATH, exist_ok=True)

    pdf_files = glob.glob(os.path.join(DATA_DIR, "*.pdf"))
    if not pdf_files:
        print(f"오류: '{DATA_DIR}' 디렉토리에서 PDF 파일을 찾을 수 없습니다.")
        if not os.listdir(CHROMA_DB_PATH):
            os.rmdir(CHROMA_DB_PATH)
        return

    all_documents = []
    try:
        # 1. 모든 PDF 로드 및 텍스트 추출
        print(f"총 {len(pdf_files)}개의 PDF 파일을 처리합니다.")
        for pdf_path in pdf_files:
            file_meta = extract_metadata_from_filename(pdf_path)
            source_name = file_meta['source']
            print(f"'{os.path.basename(pdf_path)}' 파일 처리 중... (메타데이터: {file_meta})")
            
            reader = PdfReader(pdf_path)
            documents_per_pdf = []
            for i, page in enumerate(reader.pages):
                text = page.extract_text()
                if text:
                    # 페이지별 메타데이터 생성 (파일 공통 메타데이터 + 페이지 번호)
                    page_meta = file_meta.copy()
                    page_meta["page"] = i + 1
                    
                    documents_per_pdf.append(
                        Document(
                            page_content=text,
                            metadata=page_meta
                        )
                    )
            all_documents.extend(documents_per_pdf)

        if not all_documents:
            raise ValueError("모든 PDF에서 텍스트를 추출하지 못했습니다.")

        # 2. 텍스트 분할
        print(f"\n총 {len(all_documents)}개 페이지를 청크로 분할합니다.")
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=300)
        chunked_documents = text_splitter.split_documents(all_documents)
        print(f"총 {len(chunked_documents)}개의 청크가 생성되었습니다.")

        # 3. 임베딩 및 ChromaDB 저장
        print("임베딩을 생성하고 ChromaDB에 저장합니다...")
        embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL)
        
        vectorstore = Chroma.from_documents(
            documents=chunked_documents,
            embedding=embeddings,
            collection_name=COLLECTION_NAME,
            persist_directory=CHROMA_DB_PATH,
        )

    except Exception as e:
        print(f"데이터 적재 중 오류 발생: {e}")
        print("오류가 발생하여 생성 중이던 DB를 삭제합니다.")
        if 'vectorstore' in locals():
            del vectorstore
        time.sleep(1)
        if os.path.exists(CHROMA_DB_PATH):
            shutil.rmtree(CHROMA_DB_PATH)
        return

    print("\n데이터 적재가 완료되었습니다.")
    print(f"'{CHROMA_DB_PATH}' 경로에 벡터 데이터베이스가 성공적으로 생성되었습니다.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PDF 데이터 적재 스크립트")
    parser.add_argument(
        '--force',
        action='store_true',
        help='기존 데이터베이스를 강제로 다시 생성합니다.'
    )
    args = parser.parse_args()
    
    ingest_data(force_reingest=args.force)
