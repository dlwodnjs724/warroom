"""uvicorn으로 gateway 서버 실행."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("gateway.main:app", host="0.0.0.0", port=8000, reload=True)
