import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route('/api/health')
def health():
    return {"status": "healthy", "service": "FSBO Scraper"}

@app.route('/')
def home():
    return "FSBO Scraper is running!"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
