import streamlit as st
from your_scraper_file import run_scraper

st.title("Web Scraper Preview")

# Create a text input box for a URL
target_url = st.text_input("Enter URL to scrape:", "https://example.com")

# Create a button to trigger the scraper
if st.button("Run Scraper"):
    with st.spinner("Scraping data..."):
        results = run_scraper(target_url)
        st.success("Done!")
        # Display the scraped data cleanly as a table or JSON
        st.write(results)
