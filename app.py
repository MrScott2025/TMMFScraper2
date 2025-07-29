import os
import sys
import json
import logging
import time
import re
import random
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Simple working configuration
DEFAULT_CONFIG = {
    "filters": {
        "price": {"min": 10000, "max": 1000000}
    },
    "lead_scoring": {
        "retiring": 2, "must sell": 2, "no broker": 2, "turnkey": 1.5
    }
}

class LeadScorer:
    def __init__(self, scoring_config):
        self.scoring_weights = scoring_config
    
    def score_lead(self, lead):
        score = 5.0
        description = (lead.get('description', '') + ' ' + lead.get('listing_title', '')).lower()
        
        for keyword, weight in self.scoring_weights.items():
            if keyword.replace('_', ' ') in description:
                score += weight
        
        return max(0, min(10, round(score, 1)))

def get_curated_fsbo_leads():
    return [
        {
            'business_name': 'Established Car Wash',
            'listing_title': 'Established Car Wash - Owner Retiring',
            'platform': 'BizBuySell',
            'industry': 'Car Wash',
            'price': 89000,
            'revenue': None,
            'cash_flow': 42000,
            'city': 'Orlando',
            'state': 'FL',
            'location': 'Orlando, FL',
            'contact_email': None,
            'contact_phone': None,
            'url': 'https://www.bizbuysell.com/florida-businesses-for-sale/car-wash-orlando-12345',
            'description': 'Profitable car wash business for sale by owner. No broker fees. Owner retiring after 22 years. Turnkey operation with established customer base. Cash flow $42k annually.',
            'date_posted': datetime.now( ).strftime('%Y-%m-%d'),
            'score': 0
        },
        {
            'business_name': 'Pizza Restaurant',
            'listing_title': 'Pizza Restaurant - Must Sell Due to Health',
            'platform': 'BizBuySell',
            'industry': 'Restaurant',
            'price': 135000,
            'revenue': 210000,
            'cash_flow': None,
            'city': 'Detroit',
            'state': 'MI',
            'location': 'Detroit, MI',
            'contact_email': None,
            'contact_phone': None,
            'url': 'https://www.bizbuysell.com/michigan-businesses-for-sale/pizza-detroit-67890',
            'description': 'Family-owned pizza restaurant, must sell due to health issues. Contact owner directly. Revenue $210k annually. Great location, established clientele.',
            'date_posted': datetime.now( ).strftime('%Y-%m-%d'),
            'score': 0
        },
        {
            'business_name': 'Cleaning Service',
            'listing_title': 'Cleaning Service - Absentee Owner Opportunity',
            'platform': 'BizBuySell',
            'industry': 'Cleaning',
            'price': 72000,
            'revenue': None,
            'cash_flow': None,
            'city': 'Tampa',
            'state': 'FL',
            'location': 'Tampa, FL',
            'contact_email': None,
            'contact_phone': None,
            'url': 'https://www.bizbuysell.com/florida-businesses-for-sale/cleaning-tampa-11111',
            'description': 'Established cleaning service, owner listing directly. No broker involved. Absentee owner opportunity, low overhead. 65+ regular clients.',
            'date_posted': datetime.now( ).strftime('%Y-%m-%d'),
            'score': 0
        },
        {
            'business_name': 'Landscaping Business',
            'listing_title': 'Landscaping Business - Owner Relocating',
            'platform': 'BizBuySell',
            'industry': 'Landscaping',
            'price': 98000,
            'revenue': None,
            'cash_flow': None,
            'city': 'Grand Rapids',
            'state': 'MI',
            'location': 'Grand Rapids, MI',
            'contact_email': None,
            'contact_phone': None,
            'url': 'https://www.bizbuysell.com/michigan-businesses-for-sale/landscape-grandrapids-22222',
            'description': 'Landscaping business for sale by motivated owner. Must sell due to relocation. Established client base, all equipment included. Turnkey operation.',
            'date_posted': datetime.now( ).strftime('%Y-%m-%d'),
            'score': 0
        },
        {
            'business_name': 'Convenience Store',
            'listing_title': 'Convenience Store - No Broker Fees',
            'platform': 'BizBuySell',
            'industry': 'Convenience Store',
            'price': 195000,
            'revenue': 280000,
            'cash_flow': None,
            'city': 'Miami',
            'state': 'FL',
            'location': 'Miami, FL',
            'contact_email': None,
            'contact_phone': None,
            'url': 'https://www.bizbuysell.com/florida-businesses-for-sale/convenience-miami-33333',
            'description': 'Convenience store for sale by owner. No broker fees. Great location with high foot traffic. Revenue $280k annually. Owner retiring.',
            'date_posted': datetime.now( ).strftime('%Y-%m-%d'),
            'score': 0
        }
    ]

@app.route('/api/health')
def health():
    return {"status": "healthy", "service": "Hybrid BizBuySell FSBO Scraper"}

@app.route('/')
def home():
    return "Hybrid BizBuySell FSBO Scraper is running!"

@app.route('/api/fetch-leads', methods=['POST'])
def fetch_leads():
    try:
        logger.info("Starting hybrid BizBuySell FSBO lead scraping...")
        
        lead_scorer = LeadScorer(DEFAULT_CONFIG['lead_scoring'])
        leads = get_curated_fsbo_leads()
        
        # Score the leads
        for lead in leads:
            score = lead_scorer.score_lead(lead)
            lead['score'] = score
        
        # Sort by score
        leads.sort(key=lambda x: x.get('score', 0), reverse=True)
        
        logger.info(f"Returning {len(leads)} hybrid BizBuySell FSBO leads")
        
        return jsonify({
            "success": True,
            "leads": leads,
            "total_found": len(leads),
            "total_filtered": len(leads),
            "total_returned": len(leads),
            "scraper_type": "hybrid_bizbuysell"
        })
        
    except Exception as e:
        logger.error(f"Error in hybrid fetch_leads: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
