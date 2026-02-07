# Newsletter Growth Dashboard 📈

A Streamlit application to visualize your Substack growth, identify key conversion drivers, and analyze viral spikes.

## Features

- **📊 Growth Visualization**: Interactive line chart showing total subscribers over time.
- **📍 Catalyst Overlay**: Automatically plots your published posts on the growth chart to see what drives spikes.
- **🚀 Spike Analysis**: Identifies the 3-day window with the highest percentage growth and finds the post responsible.
- **💰 Conversion Metrics**: Tracks your Free-to-Paid conversion rate.
- **🔒 Private & Secure**: All data processing happens locally in your browser/memory. No data is stored.

## Quick Start

### Run Locally

1. **Clone the repo**:
   ```bash
   git clone https://github.com/mphinance/substack-data-mining.git
   cd substack-data-mining
   ```

2. **Set up the environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Run the app**:
   ```bash
   streamlit run dashboard.py
   ```

4. **Upload Data**:
   - Export your data from Substack (Settings > Export > Export all data).
   - Upload the `.zip` file to the dashboard.

## deploy to Streamlit Cloud

1. Push this code to your GitHub repository.
2. Log in to [Streamlit Cloud](https://streamlit.io/cloud).
3. Connect your GitHub account and select this repository.
4. Streamlit will automatically detect `dashboard.py` and `requirements.txt`.
5. Click **Deploy**!

## Requirements

- Python 3.8+
- streamlit
- pandas
- plotly

## License

MIT
