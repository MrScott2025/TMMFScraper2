import os
import sys
sys.path.insert(0, os.path.dirname(__file__ ))

from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route('/api/health')
def health():
    return {"status": "healthy", "service": "FSBO Scraper"}

@app.route('/')
def home():
    return "FSBO Scraper is running!"

@app.route('/api/fetch-leads', methods=['POST'])
def fetch_leads():
    try:
        # Get request data
        config = request.get_json() if request.is_json else {}
        
        # Return sample data for now
        sample_leads = [
            {
                "business_name": "Sample Car Wash",
                "platform": "Test Platform",
                "industry": "Car Wash",
                "price": 75000,
                "revenue": 95000,
                "cash_flow": 45000,
                "location": "Orlando, FL",
                "contact_email": "test@example.com",
                "url": "https://example.com",
                "description": "Sample business for testing",
                "score": 8.5
            },
            {
                "business_name": "Test Restaurant",
                "platform": "Test Platform",
                "industry": "Restaurant",
                "price": 120000,
                "revenue": 180000,
                "cash_flow": 65000,
                "location": "Miami, FL",
                "contact_phone": "(305 ) 555-0123",
                "url": "https://example.com",
                "description": "Sample restaurant for testing",
                "score": 7.2
            }
        ]
        
        return jsonify({
            "success": True,
            "leads": sample_leads,
            "total_found": 2,
            "total_filtered": 2,
            "total_returned": 2
        } )
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
