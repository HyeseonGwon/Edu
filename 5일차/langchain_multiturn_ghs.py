from dotenv import load_dotenv
import os
from langchain_openai import ChatOpenAI 
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage 


load_dotenv()
llm = ChatOpenAI(model="gpt-4o")  # ChatOpenAI 클래스의 인스턴스 생성 


def get_ai_response(messages):
    response=llm.invoke(messages)  # 내가 틀렸던(AI가 계속 영어로만 대답했던)이유:변수 messages가 아닌 'messages'라는 문자열을 넣어서!
    return response.content  # 생성된 응답의 내용 반환
# 정답지를 보니 이 부분조차 필요가 없었다...!

messages = [
    SystemMessage(content='너는 사용자를 도와주는 상담사야. 항상 한국어로 대답해'), # 초기 시스템 메시지
     # 사용자 메시지
]

while True:
    user_input = input("사용자: ")  # 사용자 입력 받기

    if user_input == "exit":  # ② 사용자가 대화를 종료하려는지 확인인
        break
    
   
    messages.append(
        HumanMessage(content=user_input) # 사용자 메시지
    )
    ai_response = llm.invoke(messages)  # 주석처리
    
    messages.append(
        ai_response.content
    )  # AI 응답 대화 기록에 추가하기

    print("AI: " + ai_response.content)  # AI 응답 출력
    #print("AI: " + get_ai_response(messages))  # 내가 틀렸던(AI가 계속 영어로만 대답했던)이유: 함수에 messages를 변수가 아닌 문자열로 넣었엇다.
    #그리고 별도로 함수로 만들 필요 없기도 했다.
