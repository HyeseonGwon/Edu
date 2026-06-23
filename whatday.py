from datetime import datetime

def get_weekday_with_color(year, month, day):
    # ANSI Escape Code 정의 (텍스트 색상 변경)
    COLOR_RESET = "\033[0m"
    COLOR_BLUE = "\033[94m"   # 토요일용 파란색
    COLOR_RED = "\033[91m"    # 일요일용 빨간색
    
    weekday_list = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    
    try:
        target_date = datetime(year, month, day)
        weekday_num = target_date.weekday()
        weekday_name = weekday_list[weekday_num]
        
        # 토요일(5)과 일요일(6)일 때만 색상 코드 적용
        if weekday_num == 5:
            return f"{COLOR_BLUE}{weekday_name}{COLOR_RESET}"
        elif weekday_num == 6:
            return f"{COLOR_RED}{weekday_name}{COLOR_RESET}"
        else:
            return weekday_name
        
    except ValueError:
        return "올바르지 않은 날짜 형식입니다. 입력한 날짜를 확인해주세요."

# --- 사용 예시 ---
if __name__ == "__main__":
    print("년, 월, 일을 차례대로 입력하세요.")
    try:
        y = int(input("년(Year): "))
        m = int(input("월(Month): "))
        d = int(input("일(Day): "))
        
        result = get_weekday_with_color(y, m, d)
        print(f"\n👉 {y}년 {m}월 {d}일은 [{result}] 입니다.")
        
    except ValueError:
        print("숫자만 입력해주세요.")