import requests


url = "https://qatfalebanon.com"


response = requests.get(
    url,
    headers={
        "User-Agent": "Mozilla/5.0"
    }
)


html = response.text.lower()


print("HTML length:", len(html))


signals = [
    "shopify",
    "cdn.shopify.com",
    "myshopify.com",
    "shopify-section",
    "shopify-payment-button",
    "shopify.theme",
    "shopify-features"
]


for signal in signals:

    if signal in html:
        print("FOUND:", signal)

    else:
        print("Missing:", signal)