import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

import pymupdf

def pdf_to_summary(pdf_path):
    doc = pymupdf.open(pdf_path)
    text = ""
    full_text = ""
    for page in doc:
        text = page.get_text()
        full_text+=text + '\n------------------------\n'
    
    pdf_file_name = os.path.splitext(os.path.basename(pdf_path))[0]
    txt_file_path = os.path.join(os.getcwd(), 'samples', f"{pdf_file_name}.txt")
    with open(txt_file_path, 'w', encoding='utf-8') as f:
        f.write(full_text)
    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    with open(txt_file_path, 'r', encoding = 'utf-8') as f:
        txt = f.read()    

    system_prompt = f'''
    너는 다음 글을 요약하는 봇이다. 아래 글을 읽고, 

    작성해야 하는 포맷은 다음과 같음
    # 제목

    ## 저자의 문제 인식 및 주장 (15문장 이내)

    ## 저자 소개


    ============= 이하 텍스트 ================
    {txt[:10000]}

    '''

    response = client.chat.completions.create(
        model = 'gpt-4o-mini',
        temperature = 0.1,
        messages=[
            {"role":"system","content":system_prompt},
        ]
    )

    return response.choices[0].message.content

if __name__ == "__main__":
    path = r"samples\pdf_samples\A survey on large language model based autonomous agents.pdf"
    print(pdf_to_summary(path))
    print("\n------------------------\n" + "PDF 요약이 성공적으로 수행 되었습니다")