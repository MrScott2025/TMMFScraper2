from flask import Blueprint, jsonify, request
from flask_cors import cross_origin
import json
import os
import logging
from src.scrapers.craigslist_scraper import CraigslistScraper
from src.scrapers.bizbuysell_scraper import BizBuySellScraper
from src.scrapers.businessbroker_scraper import BusinessBrokerScraper
from src.scrapers.flippa_scraper import FlippaScraper
from src.utils.lead_scorer import LeadScorer
from src.utils.data_normalizer import DataNormalizer

scraper_bp = Blueprint('scraper', __name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_config():
    """Load configuration from config.json"""
    # Try multiple possible paths for config file
    possible_paths = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'config.json'),
        os.path.join(os.path.dirname(__file__), '..', '..', 'config.json'),
        'config.json',
        '/app/config.json'  # Common deployment path
    ]
    
    for config_path in possible_paths:
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    config = json.load(f)
                    logger.info(f"Loaded config from {config_path}")
                    return config
        except Exception as e:
            logger.warning(f"Failed to load config from {config_path}: {str(e)}")
            continue
    
    # If no config file found, return default configuration
    logger.warning("No config file found, using default configuration")
    return get_default_config()

def get_default_config():
    """Return default configuration if config file is not found"""
    return {
        "geo_targets": {
            "states": ["Florida", "Michigan"],
            "cities": ["Miami", "Orlando", "Tampa", "Jacksonville", "Detroit", "Grand Rapids", "Ann Arbor", "Lansing"]
        },
        "platforms": {
            "craigslist": {
                "enabled": True,
                "regions": ["miami", "orlando", "tampa", "detroit", "grandrapids"],
                "keywords": ["business for sale", "owner selling", "turnkey", "must sell"]
            },
            "bizbuysell": {
                "enabled": True,
                "filters": ["owner selling", "no broker", "fsbo", "contact owner"]
            },
            "businessbroker": {
                "enabled": True,
                "filters": ["no broker", "owner listing", "motivated seller"]
            },
            "flippa": {
                "enabled": True,
                "seller_type": "owner"
            },
            "facebook_marketplace": {
                "enabled": False
            }
        },
        "filters": {
            "price": {"min": 10000, "max": 1000000},
            "revenue": {"min": 50000},
            "cash_flow": {"min": 25000},
            "industries": ["car wash", "detailing", "cleaning", "landscaping", "HVAC", "plumbing", "ecommerce", "restaurant", "pizza", "convenience store", "gas station", "laundromat", "food truck", "mobile business"]
        },
        "lead_scoring": {
            "retiring": 2, "must sell": 2, "no broker": 2, "turnkey": 1.5,
            "low overhead": 1, "absentee owner": 1, "owner operated": 1,
            "missing_contact": -1, "price_above_max": -2
        },
        "scraper_settings": {
            "max_leads_per_run": 50,
            "request_delay": 2,
            "timeout": 30,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
    }

@scraper_bp.route('/fetch-leads', methods=['POST'])
@cross_origin()
def fetch_leads():
    """Main endpoint to fetch FSBO leads from all platforms"""
    try:
        # Load configuration
        config = load_config()
        # Config should never be None now due to default fallback
        
        # Get request data (optional config overrides)
        request_data = request.get_json() if request.is_json else {}
        
        # Initialize components
        lead_scorer = LeadScorer(config['lead_scoring'])
        data_normalizer = DataNormalizer(config['filters'])
        
        all_leads = []
        
        # Initialize scrapers
        scrapers = []
        
        if config['platforms']['craigslist']['enabled']:
            scrapers.append(CraigslistScraper(config))
            
        if config['platforms']['bizbuysell']['enabled']:
            scrapers.append(BizBuySellScraper(config))
            
        if config['platforms']['businessbroker']['enabled']:
            scrapers.append(BusinessBrokerScraper(config))
            
        if config['platforms']['flippa']['enabled']:
            scrapers.append(FlippaScraper(config))
        
        # Run scrapers
        for scraper in scrapers:
            try:
                logger.info(f"Running {scraper.__class__.__name__}")
                leads = scraper.scrape()
                logger.info(f"Found {len(leads)} leads from {scraper.__class__.__name__}")
                all_leads.extend(leads)
            except Exception as e:
                logger.error(f"Error in {scraper.__class__.__name__}: {str(e)}")
                continue
        
        # Normalize and filter leads
        normalized_leads = []
        for lead in all_leads:
            try:
                normalized_lead = data_normalizer.normalize(lead)
                if normalized_lead and data_normalizer.passes_filters(normalized_lead):
                    # Add lead score
                    score = lead_scorer.score_lead(normalized_lead)
                    normalized_lead['score'] = score
                    normalized_leads.append(normalized_lead)
            except Exception as e:
                logger.error(f"Error normalizing lead: {str(e)}")
                continue
        
        # Sort by score (highest first) and limit to max_leads_per_run
        normalized_leads.sort(key=lambda x: x.get('score', 0), reverse=True)
        max_leads = config['scraper_settings']['max_leads_per_run']
        final_leads = normalized_leads[:max_leads]
        
        logger.info(f"Returning {len(final_leads)} filtered and scored leads")
        
        return jsonify({
            "success": True,
            "leads": final_leads,
            "total_found": len(all_leads),
            "total_filtered": len(normalized_leads),
            "total_returned": len(final_leads)
        })
        
    except Exception as e:
        logger.error(f"Error in fetch_leads: {str(e)}")
        return jsonify({"error": str(e)}), 500

@scraper_bp.route('/config', methods=['GET'])
@cross_origin()
def get_config():
    """Get current configuration"""
    config = load_config()
    if config:
        return jsonify(config)
    else:
        return jsonify({"error": "Configuration not found"}), 500

@scraper_bp.route('/health', methods=['GET'])
@cross_origin()
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "service": "FSBO Scraper"})

