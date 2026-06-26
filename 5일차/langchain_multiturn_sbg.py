# from openai import OpenAI
from dotenv import load_dotenv
import os
from langchain_openai import ChatOpenAI 
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage 


load_dotenv()

llm = ChatOpenAI(model="gpt-4o")  # ChatOpenAI 클래스의 인스턴스 생성 (주석 풀기)


messages = [
    SystemMessage(content = '너는 사용자를 도와주는 상담사야.')
]

while True:
    user_input = input("사용자: ")  # 사용자 입력 받기

    if user_input == "exit":  # ② 사용자가 대화를 종료하려는지 확인인
        break
    
    messages.append(
        HumanMessage(content = user_input)
    )  
    ai_response = llm.invoke(messages)

    messages.append(ai_response)

    # )  # AI 응답 대화 기록에 추가하기

    print("AI: " + ai_response.content)  # AI 응답 출력
