// DOM 요소 선택
const todoInput = document.getElementById('todo-input');
const addBtn = document.getElementById('add-btn');
const todoList = document.getElementById('todo-list');

// 앱 시작 시 로컬스토리지에서 기존 할 일 데이터를 가져옴 (없으면 빈 배열)
let todos = JSON.parse(localStorage.getItem('todos')) || [];

// 할 일 목록을 화면에 그리는 함수
function renderTodos() {
    todoList.innerHTML = ''; // 기존 목록 초기화

    todos.forEach((todo, index) => {
        const li = document.createElement('li');
        li.className = `todo-item ${todo.completed ? 'completed' : ''}`;

        li.innerHTML = `
            <div class="todo-content">
                <input type="checkbox" ${todo.completed ? 'checked' : ''} onchange="toggleTodo(${index})">
                <span class="todo-text">${todo.text}</span>
            </div>
            <button class="delete-btn" onclick="deleteTodo(${index})">삭제</button>
        `;

        todoList.appendChild(li);
    });

    // 데이터가 바뀔 때마다 로컬스토리지에 최신 상태 저장
    saveToStorage();
}

// 로컬스토리지 저장 함수
function saveToStorage() {
    localStorage.setItem('todos', JSON.stringify(todos));
}

// 새로운 할 일 추가 함수
function addTodo() {
    const text = todoInput.value.trim();
    
    if (text === '') {
        alert('할 일을 입력해주세요!');
        return;
    }

    // 배열에 객체 형태로 추가 (내용, 완료 여부)
    todos.push({ text: text, completed: false });
    
    // 입력창 비우고 포커스 주기
    todoInput.value = '';
    todoInput.focus();

    renderTodos();
}

// 할 일 삭제 함수
function deleteTodo(index) {
    todos.splice(index, 1); // 해당 인덱스의 아이템 제거
    renderTodos();
}

// 체크박스 상태 토글 함수
function toggleTodo(index) {
    todos[index].completed = !todos[index].completed; // 상태 반전
    renderTodos();
}

// --- 이벤트 리스너 설정 ---

// 추가 버튼 클릭 시
addBtn.addEventListener('click', addTodo);

// 입력창에서 엔터 키를 눌렀을 때도 추가되도록 설정
todoInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        addTodo();
    }
});

// 앱이 처음 로드될 때 기존 할 일 화면에 표시
renderTodos();