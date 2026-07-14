from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title='Day17 Demo', version='0.1.0')

PIKACHU_URL = 'https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/25.png'

@app.get('/hello', response_class=HTMLResponse)
def hello():
    return f'''
    <html>
      <body style="text-align:center; font-family:sans-serif;">
        <h1>안녕하세요. 피카츄 귀엽죠? 환영합니다.</h1>
        <img src="{PIKACHU_URL}" alt="피카츄" width="300">
      </body>
    </html>
    '''

@app.get('/')
def welcome():
    return {'message': '엔드포인트 hello로 오세요.'}
