import os
from dotenv import load_dotenv
import pymupdf
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.tools import tool

load_dotenv()

#내부 함수는 일반 함수로 지정한다. tool 데코레이터를 사용하지 않는다.
def pdf_to_text(pdf_path):
    doc = pymupdf.open(pdf_path)
    full_text = ''
    for page in doc:
        text = page.get_text()
        full_text += text + '\n------------------------\n'

    pdf_file_name = os.path.basename(pdf_path)
    pdf_file_name = os.path.splitext(pdf_file_name)[0]
    txt_file_path = os.path.join(os.path.dirname(pdf_path), f'{pdf_file_name}.txt')
    with open(txt_file_path, 'w', encoding='utf-8') as f:
        f.write(full_text)

    return txt_file_path

def summarize_txt(txt_file_path):
    client = ChatOpenAI(model='gpt-4o-mini', temperature=0.1)
    with open(txt_file_path, 'r', encoding='utf-8') as f:
        txt = f.read()
    
    system_prompt = f'''
    너는 다음 글을 요약하는 봇이다. 아래 글을 읽고, 

    작성해야 하는 포맷은 다음과 같음
    # 제목

    ## 저자의 문제 인식 및 주장 (15문장 이내)

    ## 저자 소개

    '''

    messages=[
        SystemMessage(content=system_prompt),
        HumanMessage(content=txt[:10000]),
    ]

    response = client.invoke(messages)

    return response.content

@tool
def summarize_pdf(pdf_path):
    """
    pdf 경로를 받아 요약한 string을 반환하는 도구

    Args:
        pdf_path: pdf 경로

    """
    txt_file_path = pdf_to_text(pdf_path)
    summary = summarize_txt(txt_file_path)
    summary_file_name = os.path.splitext(os.path.basename(pdf_path))[0] + '_summary.txt'
    summary_file_path = os.path.join(os.path.dirname(pdf_path), summary_file_name)
    with open(summary_file_path, 'w', encoding='utf-8') as f:
        f.write(summary)
    return summary



if __name__ == "__main__":
    pdf_path = os.path.join(os.getcwd(),"samples/Language_models.pdf")
    summary = summarize_pdf(pdf_path)
    print(summary)
    print("성공적으로 요약되었습니다.")


