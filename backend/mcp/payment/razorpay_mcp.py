import os
import json
import re
import hmac
import hashlib
import urllib.request
import urllib.error
import base64
import uuid
import logging
from pymongo import MongoClient

logger = logging.getLogger(__name__)

_CACHED_ACCOUNT_NUMBER = None

def get_razorpay_credentials() -> dict:
    """
    Robust credential resolver.
    Returns dict with keys 'key_id', 'key_secret', and 'account_number'.
    """
    global _CACHED_ACCOUNT_NUMBER
    key_id = os.getenv("KEY_ID") or os.getenv("RAZORPAY_KEY_ID") or os.getenv("razorpay_key_id") or ""
    key_secret = os.getenv("Key_SECRET") or os.getenv("KEY_SECRET") or os.getenv("RAZORPAY_KEY_SECRET") or os.getenv("razorpay_key_secret") or ""
    account_number = os.getenv("RAZORPAYX_ACCOUNT_NUMBER") or os.getenv("ACCOUNT_NUMBER") or os.getenv("RAZORPAY_ACCOUNT_NUMBER") or ""

    if not key_id or not str(key_id).strip():
        key_id = "rzp_test_TUm5bQm8NFvqYB"
    if not key_secret or not str(key_secret).strip():
        key_secret = "JgcwBrGWyUCB48PVGsx7fPk0"

    key_id = str(key_id).strip()
    key_secret = str(key_secret).strip()

    if _CACHED_ACCOUNT_NUMBER:
        account_number = _CACHED_ACCOUNT_NUMBER
    else:
        conn_str = os.getenv("MDB_MCP_CONNECTION_STRING")
        if conn_str and not account_number:
            try:
                db_name = os.getenv("MONGODB_DATABASE", "Merchant_1")
                client = MongoClient(conn_str, connectTimeoutMS=5000, serverSelectionTimeoutMS=5000, connect=False)
                doc = client[db_name]["Merchant_Info"].find_one()
                if doc:
                    if not key_id:
                        key_id = doc.get("razorpay_key_id") or doc.get("key_id") or key_id
                    if not key_secret:
                        key_secret = doc.get("razorpay_key_secret") or doc.get("key_secret") or key_secret
                    db_acc = doc.get("account_number") or doc.get("razorpay_account_number") or doc.get("razorpayx_account_number")
                    if db_acc and str(db_acc).strip() not in ["7878780080316316", "2323230080316316"]:
                        account_number = str(db_acc).strip()
            except Exception as e:
                logger.warning(f"Error checking MongoDB for Razorpay credentials: {e}")

        # Dynamically query RazorpayX banking balances endpoint to resolve exact account number for key_id
        if not account_number or account_number in ["7878780080316316", "2323230080316316"]:
            try:
                url = "https://api.razorpay.com/v1/banking_balances"
                req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
                auth_str = f"{key_id}:{key_secret}"
                auth_bytes = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
                req.add_header("Authorization", f"Basic {auth_bytes}")
                with urllib.request.urlopen(req, timeout=3.0) as response:
                    res_data = response.read().decode("utf-8")
                    data = json.loads(res_data)
                    items = data.get("items", [])
                    if items:
                        for item in items:
                            acc = item.get("account_number")
                            if acc:
                                account_number = str(acc).strip()
                                _CACHED_ACCOUNT_NUMBER = account_number
                                logger.info(f"[RAZORPAY MCP] Dynamically resolved RazorpayX account number: {account_number}")
                                break
            except Exception as e:
                logger.warning(f"[RAZORPAY MCP] Dynamic banking balance query failed: {e}")

    if not account_number or not str(account_number).strip() or account_number in ["7878780080316316", "2323230080316316"]:
        account_number = "2323230077319715"

    _CACHED_ACCOUNT_NUMBER = account_number

    return {
        "key_id": key_id,
        "key_secret": key_secret,
        "account_number": str(account_number).strip()
    }


class RazorpayMCPConnector:

    def __init__(self):
        self.connection_string = os.getenv("MDB_MCP_CONNECTION_STRING")
        self.db_name = os.getenv("MONGODB_DATABASE", "Merchant_1")
        creds = get_razorpay_credentials()
        self.key_id = creds["key_id"]
        self.key_secret = creds["key_secret"]
        self.account_number = creds["account_number"]
        self._resolve_account_number()

    def _resolve_account_number(self):
        global _CACHED_ACCOUNT_NUMBER
        if _CACHED_ACCOUNT_NUMBER and _CACHED_ACCOUNT_NUMBER not in ["7878780080316316", "2323230080316316"]:
            self.account_number = _CACHED_ACCOUNT_NUMBER
            return

        creds = get_razorpay_credentials()
        self.account_number = creds["account_number"]
        _CACHED_ACCOUNT_NUMBER = self.account_number

    def get_merchant_details(self, merchant_id: str = None) -> dict | None:

        if not merchant_id or not self.connection_string:
            return None
            
        try:
            client = MongoClient(self.connection_string)
            db = client[self.db_name]
            
            for col_name in ["Merchant Info", "Merchant_Info"]:
                if col_name in db.list_collection_names():
                    col = db[col_name]
                    doc = col.find_one({
                        "$or": [
                            {"merchant_id": {"$regex": f"^{merchant_id}$", "$options": "i"}},
                            {"name": {"$regex": f"^{merchant_id}$", "$options": "i"}},
                            {"merchant_name": {"$regex": f"^{merchant_id}$", "$options": "i"}}
                        ]
                    })
                    if doc:
                        bank = doc.get("bank_account", {})
                        details = {
                            "account_number": bank.get("account_number") or doc.get("account_number") or "919020083204923",
                            "ifsc": bank.get("ifsc") or doc.get("ifsc") or "UTIB0000229",
                            "name": bank.get("account_holder_name") or doc.get("business_name") or doc.get("merchant_name") or doc.get("name") or "Merchant Vendor",
                            "email": doc.get("email") or "vendor@example.com",
                            "phone": doc.get("phone") or "9876543210"
                        }
                        logger.info(f"[RAZORPAY MCP] Resolved merchant details for '{merchant_id}': account={details['account_number']}, ifsc={details['ifsc']}")
                        return details
        except Exception as e:
            logger.error(f"[RAZORPAY MCP] Error resolving merchant bank details for '{merchant_id}': {e}")
            
        return None

    def create_payout(self, amount_in_inr: float, contact_name: str = "Harshit", merchant_id: str = None) -> dict:
    
        amount_paise = int(amount_in_inr * 100)
        url = "https://api.razorpay.com/v1/payouts"
        logger.info(f"[RAZORPAY MCP] Initiating payout of ₹{amount_in_inr} (merchant_id={merchant_id})")
        
        # Dynamically resolve merchant recipient details if merchant_id is specified
        merchant_details = self.get_merchant_details(merchant_id)
        if merchant_details:
            bank_acc = merchant_details.get("account_number")
            bank_ifsc = merchant_details.get("ifsc")
            if not bank_ifsc or bank_ifsc in ["HDFC0001234", "HDFC0186252", "KKBK0624595", "UTIB0304348", "SBIN0242646", "ICIC0412690"] or len(bank_ifsc) != 11:
                bank_ifsc = "UTIB0000229"
            bank_name = merchant_details.get("name") 
            bank_email = merchant_details.get("email") 
            bank_phone = merchant_details.get("phone") 
            logger.info(f"[RAZORPAY MCP] Routing payout to Merchant ({merchant_id}): {bank_name} ({bank_acc}, {bank_ifsc})")
        else:
            bank_acc = "919020083204923"
            bank_ifsc = "UTIB0000229"
            bank_name = contact_name
            bank_email = f"{contact_name.lower().replace(' ', '')}@example.com"
            bank_phone = "9876543210"
            logger.info(f"[RAZORPAY MCP] Routing payout to your own contact: {bank_name} ({bank_acc})")

        # Sanitize and truncate narration to max 30 alphanumeric characters for Razorpay requirements
        raw_narration = f"Payout {bank_name}".strip()
        clean_narration = re.sub(r'[^a-zA-Z0-9 ]', '', raw_narration)[:30].strip()
        if not clean_narration:
            clean_narration = "ShopSmart Payout"

        # Build composite payout parameters
        payload = {
            "account_number": self.account_number,
            "amount": amount_paise,
            "currency": "INR",
            "mode": "IMPS",
            "purpose": "payout",
            "queue_if_low_balance": True,
            "fund_account": {
                "account_type": "bank_account",
                "bank_account": {
                    "name": bank_name,
                    "ifsc": bank_ifsc,
                    "account_number": bank_acc
                },
                "contact": {
                    "name": bank_name,
                    "email": bank_email,
                    "contact": bank_phone,
                    "type": "vendor"
                }
            },
            "reference_id": f"ref_{uuid.uuid4().hex[:12]}",
            "narration": clean_narration
        }
        
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/json",
            "X-Payout-Idempotency-Key": uuid.uuid4().hex
        })
        
        auth_str = f"{self.key_id}:{self.key_secret}"
        auth_bytes = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
        req.add_header("Authorization", f"Basic {auth_bytes}")
        
        try:
            with urllib.request.urlopen(req) as response:
                res_data = response.read().decode("utf-8")
                payout = json.loads(res_data)
                logger.info(f"[RAZORPAY MCP] Payout successful: payout_id={payout.get('id')}, status={payout.get('status')}, utr={payout.get('utr')}")
                return {
                    "success": True,
                    "payout_id": payout.get("id"),
                    "amount": payout.get("amount") / 100.0,
                    "currency": payout.get("currency"),
                    "status": payout.get("status"),
                    "utr": payout.get("utr"),
                    "key_id": self.key_id,
                    "account_number": self.account_number,
                    "merchant": {
                        "name": bank_name,
                        "account_number": bank_acc,
                        "ifsc": bank_ifsc,
                        "email": bank_email,
                        "phone": bank_phone
                    }
                }
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            logger.error(f"[RAZORPAY MCP] Payout failed with HTTP {e.code}: {err_body}")
            try:
                err_json = json.loads(err_body)
                error_msg = err_json.get("error", {}).get("description", err_body)
            except Exception:
                error_msg = err_body
            return {
                "success": False,
                "error": error_msg,
                "code": e.code
            }
        except Exception as e:
            logger.error(f"[RAZORPAY MCP] Payout exception: {e}")
            return {
                "success": False,
                "error": str(e)
            }
