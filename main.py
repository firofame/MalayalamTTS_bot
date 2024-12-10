from fastapi import FastAPI, Request

app = FastAPI()


@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.post("/telegram")
async def telegram(request: Request):
    data = await request.json()
    print(data)  # This will print the request payload to the console
    return {"status": "success", "message": "Data received"}
