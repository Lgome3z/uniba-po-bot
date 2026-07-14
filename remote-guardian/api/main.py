from fastapi import FastAPI

app = FastAPI(title="Remote Guardian Edge API")

@app.get("/")
def read_root():
    return {"status": "online", "message": "Remote Guardian Gateway is active."}

@app.get("/health")
def health_check():
    # Later, we will add logic here to check the M5Stack connection
    return {"system": "healthy", "camera": "pending", "m5stack": "pending"}
