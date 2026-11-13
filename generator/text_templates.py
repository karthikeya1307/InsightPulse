"""Synthetic text building blocks.

All text is written for this project (no third-party corpora). A feedback text is
assembled from *clauses*; every clause carries a (theme, polarity) tag, which is
how ground-truth labels are derived by rule rather than random assignment (see
docs/business_rules.md, section "Label derivation rules").

Slots: {product} {feature} {days} {amount} {order} {weeks}
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Product catalogue (category -> products) and category-specific terminology
# ---------------------------------------------------------------------------
CATEGORY_FEATURES: dict[str, list[str]] = {
    "Electronics": ["battery life", "sound quality", "display", "charging port", "bluetooth pairing",
                    "build quality", "microphone"],
    "Home & Kitchen": ["motor", "lid", "non-stick coating", "blades", "heating element", "jar"],
    "Fashion": ["fabric", "stitching", "fit", "colour", "sole", "zip"],
    "Beauty & Personal Care": ["fragrance", "texture", "pump dispenser", "formula", "nozzle"],
    "Grocery": ["freshness", "taste", "aroma", "grain quality", "seal"],
}

# (name, category, brand, price INR). Ordered so the first N cover all categories.
PRODUCT_CATALOG: list[tuple[str, str, str, float]] = [
    ("Voltix Pulse Wireless Earbuds", "Electronics", "Voltix", 2499.0),
    ("NimbusHome Turbo Mixer Grinder", "Home & Kitchen", "NimbusHome", 3899.0),
    ("Saarang Cotton Kurta", "Fashion", "Saarang", 1299.0),
    ("Aarohi Vitamin C Face Serum", "Beauty & Personal Care", "Aarohi", 649.0),
    ("Annapurna Organic Basmati Rice 5kg", "Grocery", "Annapurna", 899.0),
    ("Voltix Fit Smartwatch S2", "Electronics", "Voltix", 4999.0),
    ("NimbusHome Air Fryer 4L", "Home & Kitchen", "NimbusHome", 5499.0),
    ("Tejas Stride Running Shoes", "Fashion", "Tejas", 2799.0),
    ("Aarohi SPF 50 Sunscreen", "Beauty & Personal Care", "Aarohi", 499.0),
    ("Annapurna Cold-Pressed Groundnut Oil 1L", "Grocery", "Annapurna", 349.0),
    ("Voltix 20000mAh Power Bank", "Electronics", "Voltix", 1799.0),
    ("NimbusHome Pressure Cooker 5L", "Home & Kitchen", "NimbusHome", 2199.0),
    ("Saarang Denim Jacket", "Fashion", "Saarang", 2599.0),
    ("Kuvira Ionic Hair Dryer", "Beauty & Personal Care", "Kuvira", 1899.0),
    ("Chaiwala Assam Green Tea 100 Bags", "Grocery", "Chaiwala", 399.0),
    ("Voltix Boom Bluetooth Speaker", "Electronics", "Voltix", 3299.0),
    ("NimbusHome RO Water Purifier", "Home & Kitchen", "NimbusHome", 12999.0),
    ("Tejas Trail Sandals", "Fashion", "Tejas", 1499.0),
    ("Kuvira Herbal Shampoo 650ml", "Beauty & Personal Care", "Kuvira", 449.0),
    ("Chaiwala Masala Chai Premix", "Grocery", "Chaiwala", 299.0),
    ("Voltix Nova 5G Smartphone", "Electronics", "Voltix", 18999.0),
    ("NimbusHome Induction Cooktop", "Home & Kitchen", "NimbusHome", 2999.0),
    ("Saarang Silk Saree", "Fashion", "Saarang", 6499.0),
    ("Aarohi Aloe Gel 200ml", "Beauty & Personal Care", "Aarohi", 299.0),
    ("Annapurna Multigrain Atta 10kg", "Grocery", "Annapurna", 599.0),
    ("Voltix NeckBand Pro", "Electronics", "Voltix", 1399.0),
    ("NimbusHome Steam Iron", "Home & Kitchen", "NimbusHome", 1599.0),
    ("Tejas Formal Leather Shoes", "Fashion", "Tejas", 3499.0),
    ("Kuvira Beard Trimmer", "Beauty & Personal Care", "Kuvira", 1299.0),
    ("Chaiwala Filter Coffee Powder", "Grocery", "Chaiwala", 449.0),
    ("Voltix Tab 10 Tablet", "Electronics", "Voltix", 14999.0),
    ("NimbusHome Electric Kettle", "Home & Kitchen", "NimbusHome", 1199.0),
    ("Saarang Linen Shirt", "Fashion", "Saarang", 1699.0),
    ("Aarohi Rose Face Wash", "Beauty & Personal Care", "Aarohi", 249.0),
    ("Annapurna Toor Dal 2kg", "Grocery", "Annapurna", 389.0),
    ("Voltix Smart Plug", "Electronics", "Voltix", 899.0),
    ("NimbusHome Ceiling Fan BLDC", "Home & Kitchen", "NimbusHome", 3799.0),
    ("Tejas Kids School Shoes", "Fashion", "Tejas", 1199.0),
    ("Kuvira Onion Hair Oil", "Beauty & Personal Care", "Kuvira", 399.0),
    ("Chaiwala Honey 500g", "Grocery", "Chaiwala", 349.0),
]

# ---------------------------------------------------------------------------
# English clauses: theme code -> polarity ("pos" | "neg" | "neu") -> templates
# ---------------------------------------------------------------------------
EN_CLAUSES: dict[str, dict[str, list[str]]] = {
    "PRODUCT_QUALITY": {
        "pos": [
            "The {feature} of the {product} is excellent",
            "Really impressed with the build and the {feature}",
            "Quality is top notch, the {feature} works perfectly",
            "The {product} feels premium and the {feature} has held up well after {weeks} weeks",
            "Superb quality, the {feature} exceeded my expectations",
        ],
        "neg": [
            "The {feature} stopped working within {days} days",
            "Poor quality, the {feature} broke after a week",
            "The {product} is defective, the {feature} is faulty",
            "Very disappointed with the {feature}, it feels cheap and flimsy",
            "The {feature} is terrible and the product feels badly made",
        ],
        "neu": [
            "The {feature} is as described in the listing",
            "The {product} does what it says, the {feature} is standard",
            "Have been using the {product} for {weeks} weeks now",
        ],
        "sarcasm": [
            "Wow, the {feature} lasted a whole {days} days, truly built to last",
            "Fantastic quality, the {feature} fell apart before I even used it properly. Just brilliant",
        ],
        "suggestion": [
            "It would help if the {feature} came with a longer warranty",
            "Please consider improving the {feature} in the next version",
        ],
    },
    "PRICING": {
        "pos": [
            "Great value for money at {amount} rupees",
            "The price is very reasonable for what you get",
            "Got it at a good discount, totally worth the price",
            "Affordable and worth every rupee",
        ],
        "neg": [
            "Way too expensive for this quality",
            "The price went up by {amount} rupees in a month, not worth it",
            "Overpriced compared to other brands",
            "Not worth {amount} rupees at all",
        ],
        "neu": [
            "The price is similar to other options in the market",
            "Paid {amount} rupees, which is the usual price",
        ],
        "sarcasm": [
            "Love paying {amount} rupees extra for the same product, what a bargain",
        ],
        "suggestion": [
            "A loyalty discount for repeat buyers would be nice",
        ],
    },
    "DELIVERY": {
        "pos": [
            "Delivery was super fast, arrived in {days} days",
            "The courier was polite and delivered on time",
            "Delivered a day earlier than promised",
            "Quick delivery and accurate tracking updates",
        ],
        "neg": [
            "Delivery was delayed by {days} days with no updates",
            "The courier never showed up and marked it as delivered",
            "Order {order} arrived {days} days late",
            "Terrible delivery experience, the tracking was wrong the whole time",
            "My package was left outside in the rain, awful delivery service",
        ],
        "neu": [
            "Delivery took {days} days, as estimated",
            "The package arrived on the expected date",
        ],
        "sarcasm": [
            "Great, my order arrived {days} days late. Just what I needed",
            "Amazing how the delivery takes {days} days when it says two",
        ],
        "suggestion": [
            "Please add an option to choose a delivery time slot",
        ],
    },
    "CUSTOMER_SUPPORT": {
        "pos": [
            "The support agent was very helpful and resolved my issue quickly",
            "Customer care was patient and polite",
            "Support team went above and beyond to help me",
            "Got a quick and helpful response from the helpline",
        ],
        "neg": [
            "Customer support is useless, nobody responds",
            "I have called the helpline {days} times and the issue is still not resolved",
            "The agent was rude and disconnected the call",
            "Support keeps giving me the same scripted reply, very frustrating",
        ],
        "neu": [
            "I contacted support about my order {order}",
            "The agent asked me to wait for an update",
        ],
        "sarcasm": [
            "Wonderful support, I only had to explain the problem {days} times",
        ],
        "suggestion": [
            "A callback option for support would be useful",
        ],
    },
    "USER_EXPERIENCE": {
        "pos": [
            "The app is easy to use and the checkout is smooth",
            "Love the clean design of the website",
            "Finding products is quick and intuitive",
            "Very user friendly interface",
        ],
        "neg": [
            "The app is confusing and hard to navigate",
            "Too many steps to place a simple order, very annoying",
            "The website layout is cluttered and frustrating",
            "Search results are irrelevant and the filters are useless",
        ],
        "neu": [
            "I mostly use the mobile app to order",
            "The website has a new layout this month",
        ],
        "sarcasm": [
            "Love how the app hides the order history so well, really intuitive",
        ],
        "suggestion": [
            "Please add a dark mode to the app",
            "A wishlist feature in the app would be helpful",
        ],
    },
    "PAYMENT": {
        "pos": [
            "UPI payment went through instantly",
            "Checkout and payment were seamless",
            "Easy payment with multiple options including cards and UPI",
        ],
        "neg": [
            "My payment failed but the money was deducted",
            "I was charged twice for the same order",
            "UPI payment keeps failing at checkout",
            "The card payment got stuck and the amount of {amount} rupees is still not reversed",
        ],
        "neu": [
            "I paid using UPI",
            "Paid by card at checkout",
        ],
        "sarcasm": [
            "Brilliant, charged twice for one order. Love the extra surprise on my bank statement",
        ],
        "suggestion": [
            "Please support more wallet options at checkout",
        ],
    },
    "PRODUCT_AVAILABILITY": {
        "pos": [
            "Glad it was finally back in stock",
            "All sizes and colours were available",
            "Good to see it always in stock",
        ],
        "neg": [
            "Always out of stock when I want to buy it",
            "My size is never available",
            "It has been sold out for {weeks} weeks with no restock date",
            "The item was cancelled after ordering because it was out of stock",
        ],
        "neu": [
            "I checked availability before ordering",
            "Some variants show limited stock",
        ],
        "sarcasm": [
            "Love the out of stock message, I see it every single week",
        ],
        "suggestion": [
            "Please add a notify me option when items are restocked",
        ],
    },
    "PACKAGING": {
        "pos": [
            "Packaging was neat and secure",
            "Loved the eco-friendly packaging",
            "The box was well sealed and protected the item",
        ],
        "neg": [
            "The box was damaged and the seal was broken",
            "Packaging was poor, the item arrived crushed",
            "Too much plastic in the packaging and it was torn",
            "The product leaked because the packaging was not sealed properly",
        ],
        "neu": [
            "It came in a standard cardboard box",
            "The packaging had the usual labels",
        ],
        "sarcasm": [
            "Nice touch sending it in a torn box, really premium unboxing",
        ],
        "suggestion": [
            "Please use less plastic in the packaging",
        ],
    },
    "REFUNDS_RETURNS": {
        "pos": [
            "Return pickup was quick and the refund came in {days} days",
            "Hassle-free replacement process",
            "Refund was processed immediately",
        ],
        "neg": [
            "Still waiting for my refund after {days} days",
            "The return pickup was cancelled {days} times",
            "They rejected my return request without any reason",
            "Refund of {amount} rupees has not been credited yet",
        ],
        "neu": [
            "I requested a return for order {order}",
            "The return window is {days} days",
        ],
        "sarcasm": [
            "Fantastic refund process, only {weeks} weeks and still counting",
        ],
        "suggestion": [
            "Please show the refund status in the app",
        ],
    },
    "TECHNICAL_ISSUES": {
        "pos": [
            "The latest app update fixed the crashes",
            "Login is fast and the app runs without errors",
            "The firmware update improved the {feature}",
        ],
        "neg": [
            "The app crashes every time I open my orders",
            "Cannot log in, it keeps showing an error",
            "After the update the {feature} keeps disconnecting",
            "The device firmware update failed and now it does not turn on",
            "OTP never arrives so I cannot log in",
        ],
        "neu": [
            "I updated the app to the latest version",
            "The app asked me to log in again after the update",
        ],
        "sarcasm": [
            "Love the new update, the app now crashes twice as fast",
        ],
        "suggestion": [
            "Please add an offline mode for the order history",
        ],
    },
}

# Theme-free ambiguous statements (label rule: NEUTRAL, no theme).
AMBIGUOUS_EN: list[str] = [
    "It's fine I guess",
    "Not sure how I feel about this yet",
    "Could be better, could be worse",
    "It is what it is",
    "Hmm, okay",
    "Will see how it goes",
    "Nothing much to say",
]

# Theme-free overall opinions. They carry polarity only, so they never add a theme
# label; without them "love"/"perfect" would appear almost only in sarcastic clauses.
OVERALL_EN: dict[str, list[str]] = {
    "pos": [
        "Absolutely love this {product}", "I love it", "Really happy with this purchase",
        "It works perfectly", "Best purchase I have made this year", "Exactly what I wanted",
        "Highly recommend the {product}", "Totally satisfied", "Amazing experience overall",
        "Love the {product}, no regrets",
    ],
    "neg": [
        "I regret buying the {product}", "Worst purchase I have made this year", "Completely useless",
        "Hate the {product}, do not buy", "Total waste of money", "Not happy with this purchase at all",
        "Awful experience overall", "I would not recommend the {product} to anyone",
    ],
}
OPENERS_EN: list[str] = ["", "", "", "Honestly, ", "Overall, ", "To be fair, ", "Well, ", "So ", "Update: "]
CLOSERS_EN: dict[str, list[str]] = {
    "POSITIVE": ["", "", "Would recommend.", "Very happy.", "Keep it up!", "Five stars from me."],
    "NEGATIVE": ["", "", "Very disappointed.", "Not buying again.", "Fix this please.", "Really unhappy."],
    "NEUTRAL": ["", "", "", "Thanks."],
    "MIXED": ["", "", "Mixed feelings overall.", "Hope they improve."],
}
CONNECTORS_EN = {"contrast": [" but ", ", however ", ". On the other hand, ", ", although "],
                 "same": [". Also, ", " and ", ". ", ", plus "]}

REVIEW_TITLES: dict[str, list[str]] = {
    "POSITIVE": ["Loved it", "Excellent purchase", "Highly recommended", "Worth it", "Great product"],
    "NEGATIVE": ["Very disappointed", "Do not buy", "Waste of money", "Not as expected", "Terrible experience"],
    "NEUTRAL": ["Average", "Okay product", "As described", "Decent", "No complaints, no praise"],
    "MIXED": ["Good and bad", "Mixed experience", "Almost there", "Pros and cons", "Could be better"],
}

SURVEY_QUESTIONS: dict[str, str] = {
    "CSAT": "How satisfied are you with your recent shopping experience? Please tell us why.",
    "NPS": "How likely are you to recommend us to a friend? What is the main reason for your score?",
    "PRODUCT_FEEDBACK": "What do you think about the product you purchased?",
    "POST_SUPPORT": "How was your experience with our support team?",
}

# ---------------------------------------------------------------------------
# Hindi and Telugu. Native-script and romanised (code-mixed) frames with a
# theme noun slot. Frames were chosen to be grammatically neutral w.r.t. gender.
# ---------------------------------------------------------------------------
THEME_NOUNS: dict[str, dict[str, str]] = {
    # theme: {hi, hi_rom, te, te_rom}
    "PRODUCT_QUALITY": {"hi": "प्रोडक्ट की क्वालिटी", "hi_rom": "product ki quality",
                        "te": "ప్రోడక్ట్ క్వాలిటీ", "te_rom": "product quality"},
    "PRICING": {"hi": "कीमत", "hi_rom": "price", "te": "ధర", "te_rom": "price"},
    "DELIVERY": {"hi": "डिलीवरी", "hi_rom": "delivery", "te": "డెలివరీ", "te_rom": "delivery"},
    "CUSTOMER_SUPPORT": {"hi": "कस्टमर सपोर्ट", "hi_rom": "customer support",
                         "te": "కస్టమర్ సపోర్ట్", "te_rom": "customer support"},
    "USER_EXPERIENCE": {"hi": "ऐप का इस्तेमाल", "hi_rom": "app use karna",
                        "te": "యాప్ వాడకం", "te_rom": "app vaadakam"},
    "PAYMENT": {"hi": "पेमेंट", "hi_rom": "payment", "te": "పేమెంట్", "te_rom": "payment"},
    "PRODUCT_AVAILABILITY": {"hi": "स्टॉक की उपलब्धता", "hi_rom": "stock availability",
                             "te": "స్టాక్ లభ్యత", "te_rom": "stock availability"},
    "PACKAGING": {"hi": "पैकेजिंग", "hi_rom": "packaging", "te": "ప్యాకేజింగ్", "te_rom": "packaging"},
    "REFUNDS_RETURNS": {"hi": "रिफंड और रिटर्न", "hi_rom": "refund aur return",
                        "te": "రిఫండ్ మరియు రిటర్న్", "te_rom": "refund and return"},
    "TECHNICAL_ISSUES": {"hi": "ऐप में तकनीकी दिक्कत", "hi_rom": "app ka technical issue",
                         "te": "యాప్ లో టెక్నికల్ సమస్య", "te_rom": "app technical problem"},
}

# For TECHNICAL_ISSUES the noun itself denotes a problem, so dedicated clauses are used.
ML_FRAMES: dict[str, dict[str, list[str]]] = {
    "hi": {
        "pos": ["{noun} के साथ मेरा अनुभव बहुत अच्छा रहा", "{noun} से मैं बहुत खुश हूँ",
                "{noun} शानदार रहा"],
        "neg": ["{noun} के साथ मेरा अनुभव बहुत खराब रहा", "{noun} को लेकर बहुत निराशा हुई",
                "{noun} बिल्कुल बेकार रहा"],
        "neu": ["{noun} ठीक-ठाक रहा", "{noun} के बारे में कोई खास राय नहीं है"],
        "contrast": ["लेकिन", "पर"],
        "same": ["और", "साथ ही"],
    },
    "hi_rom": {
        "pos": ["{noun} ka experience bahut accha raha", "{noun} se main bahut khush hoon",
                "{noun} ekdum mast tha"],
        "neg": ["{noun} ka experience bahut kharab raha", "{noun} bilkul bekaar tha",
                "{noun} se bahut pareshani hui"],
        "neu": ["{noun} theek thaak tha", "{noun} ke baare mein koi khaas raay nahi"],
        "contrast": ["lekin", "par"],
        "same": ["aur", "saath hi"],
    },
    "te": {
        "pos": ["{noun} తో నా అనుభవం చాలా బాగుంది", "{noun} విషయంలో చాలా సంతోషంగా ఉన్నాను",
                "{noun} అద్భుతంగా ఉంది"],
        "neg": ["{noun} తో నా అనుభవం చాలా చెడ్డగా ఉంది", "{noun} విషయంలో చాలా నిరాశ చెందాను",
                "{noun} అస్సలు బాగాలేదు"],
        "neu": ["{noun} పరవాలేదు", "{noun} గురించి ప్రత్యేక అభిప్రాయం లేదు"],
        "contrast": ["కానీ"],
        "same": ["మరియు"],
    },
    "te_rom": {
        "pos": ["{noun} chala bagundi", "{noun} tho chala happy ga unnanu", "{noun} super ga undi"],
        "neg": ["{noun} assalu baledu", "{noun} tho chala problem ayindi", "{noun} chala worst ga undi"],
        "neu": ["{noun} parledu", "{noun} gurinchi special ga emi ledu"],
        "contrast": ["kani"],
        "same": ["inka"],
    },
}

TECH_ML: dict[str, dict[str, list[str]]] = {
    "hi": {"pos": ["ऐप अब बिना किसी दिक्कत के चल रहा है"], "neg": ["ऐप बार-बार क्रैश हो रहा है", "लॉगिन नहीं हो रहा, एरर आ रहा है"],
           "neu": ["मैंने ऐप अपडेट किया"]},
    "hi_rom": {"pos": ["app ab bina problem ke chal raha hai"], "neg": ["app baar baar crash ho raha hai", "login nahi ho raha, error aa raha hai"],
               "neu": ["maine app update kiya"]},
    "te": {"pos": ["యాప్ ఇప్పుడు ఎలాంటి సమస్య లేకుండా పనిచేస్తోంది"], "neg": ["యాప్ పదే పదే క్రాష్ అవుతోంది", "లాగిన్ అవ్వడం లేదు, ఎర్రర్ వస్తోంది"],
           "neu": ["నేను యాప్ అప్డేట్ చేశాను"]},
    "te_rom": {"pos": ["app ippudu problem lekunda work avtundi"], "neg": ["app malli malli crash avtundi", "login avvatledu, error vastundi"],
               "neu": ["nenu app update chesanu"]},
}

AMBIGUOUS_ML: dict[str, list[str]] = {
    "hi": ["ठीक है, देखते हैं", "कुछ कह नहीं सकते"],
    "hi_rom": ["theek hai, dekhte hain", "kuch keh nahi sakte"],
    "te": ["సరే, చూద్దాం", "ఏమి చెప్పలేను"],
    "te_rom": ["sare, chuddam", "emi cheppalenu"],
}

# ---------------------------------------------------------------------------
# Support conversations
# ---------------------------------------------------------------------------
SUPPORT_OPENERS_EN = ["Hi team, ", "Hello, ", "Dear support, ", "", "Hi, regarding order {order}: "]
SUPPORT_SUBJECTS: dict[str, list[str]] = {
    "PRODUCT_QUALITY": ["Defective product received", "Product stopped working"],
    "PRICING": ["Price discrepancy on my order", "Charged more than listed price"],
    "DELIVERY": ["Order not delivered", "Delivery delayed"],
    "CUSTOMER_SUPPORT": ["Complaint about previous support interaction", "No response to my request"],
    "USER_EXPERIENCE": ["Unable to find option in app", "Website navigation problem"],
    "PAYMENT": ["Payment failed but amount deducted", "Double charge on card"],
    "PRODUCT_AVAILABILITY": ["Order cancelled due to stock", "Restock date enquiry"],
    "PACKAGING": ["Damaged package received", "Leaking package"],
    "REFUNDS_RETURNS": ["Refund not received", "Return pickup not scheduled"],
    "TECHNICAL_ISSUES": ["App crashing", "Login error"],
}
SUPPORT_FOLLOWUP: dict[str, list[tuple[str, str]]] = {
    # polarity -> list of (text, extra_theme or "")
    "pos": [("Thanks a lot, the issue is resolved now", "CUSTOMER_SUPPORT"),
            ("Thank you for the quick help, everything works now", "CUSTOMER_SUPPORT"),
            ("Appreciate the support, problem solved", "CUSTOMER_SUPPORT")],
    "neg": [("This is the third time I am writing and still no solution", "CUSTOMER_SUPPORT"),
            ("Your reply did not help at all, the problem is still there", "CUSTOMER_SUPPORT"),
            ("Still waiting, nobody has called me back", "CUSTOMER_SUPPORT")],
    "neu": [("Okay, I have shared the details you asked for", "CUSTOMER_SUPPORT"),
            ("Noted, I will wait for the update", "CUSTOMER_SUPPORT")],
}
AGENT_REPLIES = [
    "Thank you for reaching out. We are sorry for the inconvenience and have escalated your case {ticket}.",
    "Hi, we have checked your order {order}. Our team will update you within 24 hours.",
    "We apologise for the trouble. Could you please share a photo of the issue?",
    "Your request has been registered under {ticket}. We will get back to you shortly.",
    "We have initiated the required action for {ticket}. Please allow 3-5 working days.",
]

# Privacy-leak sentences injected by the anomaly injector (synthetic PII only).
PII_SENTENCES = [
    " You can reach me at {email} or call {phone}.",
    " My name is {full_name}, phone {phone}, please call back.",
    " Contact me on {email} for the details.",
    " This is {full_name} ({email}).",
]

# Labels outside the taxonomy used by the anomaly injector.
INVALID_THEME_LABELS = ["THEME-000011", "THEME-000099", "SHIPPING_SPEED", "Brand Loyalty", "THEME-00003"]

# Notes used for *explained* rating/text mismatches (legitimate boundary cases).
EXPLAINED_MISMATCH_NOTE = "Star rating contradicts the text (likely mis-click); label follows the text per guideline G-3."

FIRST_NAMES = ["Aarav", "Vivaan", "Aditya", "Sai", "Arjun", "Reyansh", "Krishna", "Ishaan", "Rohan", "Kiran",
               "Ananya", "Diya", "Saanvi", "Aadhya", "Lakshmi", "Priya", "Sneha", "Kavya", "Meera", "Pooja",
               "Rahul", "Vikram", "Suresh", "Ravi", "Venkat", "Srinivas", "Harsha", "Nikhil", "Deepak", "Manoj",
               "Divya", "Swathi", "Bhavana", "Sravani", "Neha", "Anjali", "Ritu", "Keerthi", "Sowmya", "Farah"]
LAST_NAMES = ["Sharma", "Verma", "Reddy", "Rao", "Naidu", "Gupta", "Patel", "Iyer", "Nair", "Singh",
              "Kumar", "Chowdary", "Varma", "Joshi", "Mehta", "Agarwal", "Pillai", "Das", "Khan", "Mishra",
              "Yadav", "Goud", "Shetty", "Bose", "Kapoor", "Desai", "Menon", "Prasad", "Murthy", "Sinha"]
CITIES = [  # (city, state, language preference distribution en/hi/te)
    ("Hyderabad", "Telangana", (0.45, 0.10, 0.45)),
    ("Warangal", "Telangana", (0.30, 0.05, 0.65)),
    ("Vijayawada", "Andhra Pradesh", (0.35, 0.05, 0.60)),
    ("Visakhapatnam", "Andhra Pradesh", (0.40, 0.05, 0.55)),
    ("New Delhi", "Delhi", (0.50, 0.50, 0.0)),
    ("Lucknow", "Uttar Pradesh", (0.35, 0.65, 0.0)),
    ("Jaipur", "Rajasthan", (0.40, 0.60, 0.0)),
    ("Patna", "Bihar", (0.35, 0.65, 0.0)),
    ("Mumbai", "Maharashtra", (0.70, 0.30, 0.0)),
    ("Pune", "Maharashtra", (0.75, 0.25, 0.0)),
    ("Bengaluru", "Karnataka", (0.85, 0.08, 0.07)),
    ("Chennai", "Tamil Nadu", (0.92, 0.04, 0.04)),
    ("Kolkata", "West Bengal", (0.85, 0.15, 0.0)),
    ("Ahmedabad", "Gujarat", (0.75, 0.25, 0.0)),
]
