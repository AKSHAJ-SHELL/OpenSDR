from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from craftsman.api.deps import get_db
from craftsman.compliance.unsubscribe import CONFIRM_HTML, process_unsubscribe

router = APIRouter(tags=["public"])


@router.get("/u/{token}", response_class=HTMLResponse)
def unsubscribe(token: str, db: Session = Depends(get_db)):
    email = process_unsubscribe(db, token)
    if email is None:
        return HTMLResponse("<h3>Link expired or invalid.</h3>", status_code=404)
    return HTMLResponse(CONFIRM_HTML.format(email=email))


@router.post("/u/{token}", response_class=HTMLResponse)
def unsubscribe_one_click(token: str, db: Session = Depends(get_db)):
    """RFC 8058 List-Unsubscribe-Post one-click target."""
    email = process_unsubscribe(db, token)
    if email is None:
        return HTMLResponse("not found", status_code=404)
    return HTMLResponse("ok")
