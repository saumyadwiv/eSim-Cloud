# app.py

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from simulate import run_simulation

app = FastAPI()

class SimulationRequest(BaseModel):
    netlist: str

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/simulate")
def simulate(req: SimulationRequest):
    if not req.netlist.strip():
        raise HTTPException(status_code=400, detail="Netlist cannot be empty")

    result = run_simulation(req.netlist)
    return result