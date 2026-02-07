import streamlit as st
import pandas as pd
import zipfile
import plotly.express as px
import plotly.graph_objects as go
from io import BytesIO

# --- Page Config ---
st.set_page_config(
    page_title="Newsletter Growth Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- Custom CSS for Dark Mode & Styling ---
st.markdown("""
<style>
    .stMetric {
        background-color: #262730;
        padding: 15px;
        border-radius: 10px;
        border: 1px solid #464b59;
    }
    .metric-container {
        display: flex;
        justify_content: center;
        align-items: center;
    }
</style>
""", unsafe_allow_html=True)

# --- Helper Functions ---

@st.cache_data(show_spinner=False)
def load_data(uploaded_file):
    """Extracts and loads data from the uploaded zip file."""
    posts_df = None
    subscribers_df = None
    
    try:
        with zipfile.ZipFile(uploaded_file) as z:
            # List all files
            files = z.namelist()
            
            # Find posts file
            posts_file = next((f for f in files if f.endswith('posts.csv') and '__MACOSX' not in f), None)
            if posts_file:
                with z.open(posts_file) as f:
                    posts_df = pd.read_csv(f)
            
            # Find subscribers file (could be subscribers.csv or email_list.csv)
            subs_file = next((f for f in files if ('subscribers' in f or 'email_list' in f) and f.endswith('.csv') and '__MACOSX' not in f), None)
            if subs_file:
                with z.open(subs_file) as f:
                    subscribers_df = pd.read_csv(f)
                    
    except zipfile.BadZipFile:
        st.error("The uploaded file is not a valid zip archive.")
        return None, None
    except Exception as e:
        st.error(f"Error processing file: {e}")
        return None, None
    
    return posts_df, subscribers_df

def process_subscribers(df):
    """Cleans and processes subscriber data."""
    if df is None:
        return None
    
    df = df.copy()
    
    # Normalize column names if needed (not implemented here but good for future)
    
    # Ensure datetime
    if 'created_at' in df.columns:
        df['created_at'] = pd.to_datetime(df['created_at'])
    else:
        st.error("Missing 'created_at' column in subscribers file.")
        return None
    
    # Sort by date
    df = df.sort_values('created_at')
    
    # Calculate cumulative total subscribers
    df['total_subscribers'] = range(1, len(df) + 1)
    
    return df

def process_posts(df):
    """Cleans and processes posts data."""
    if df is None:
        return None
    
    df = df.copy()
    
    # Ensure datetime
    if 'post_date' in df.columns:
        df['post_date'] = pd.to_datetime(df['post_date'])
    else:
         st.error("Missing 'post_date' column in posts file.")
         return None

    # Filter for published posts
    if 'is_published' in df.columns:
        # Handle boolean or string 'true'/'false'
        # Convert to string, lower, checkout 'true'
        df['is_published'] = df['is_published'].astype(str).str.lower() == 'true'
        df = df[df['is_published'] == True]
    
    return df.sort_values('post_date')

# --- Sidebar ---
with st.sidebar:
    st.header("Instructions")
    st.markdown("""
    1. Go to your **Substack Dashboard**.
    2. Navigate to **Settings** > **Export**.
    3. Click **"Export all data"** to download a `.zip` file.
    4. Upload that `.zip` file here.
    """)
    st.info("🔒 **Privacy Note:** Your data is processed entirely in your browser memory and is never saved to any server.")
    
    st.divider()
    st.markdown("Created with ❤️ for writers.")

# --- Main Page ---

st.title("📈 Newsletter Growth Dashboard")
st.markdown("Visualize your subscriber records and identify what drives your growth.")

uploaded_file = st.file_uploader("Upload Substack Export (.zip)", type="zip", help="Upload the zip file directly from Substack.")

if uploaded_file:
    with st.spinner("Processing data..."):
        posts_raw, subs_raw = load_data(uploaded_file)
        
    if posts_raw is not None and subs_raw is not None:
        # Process Data
        subs_df = process_subscribers(subs_raw)
        posts_df = process_posts(posts_raw)
        
        if subs_df is not None:
            # --- Metrics ---
            
            # Create columns for metrics
            m1, m2, m3 = st.columns(3)
            
            total_subs = len(subs_df)
            
            with m1:
                st.metric("Total Subscribers", f"{total_subs:,}")
            
            with m2:
                if 'active_subscription' in subs_df.columns:
                    is_paid = subs_df['active_subscription'].astype(str).str.lower() == 'true'
                    paid_count = is_paid.sum()
                    conversion_rate = (paid_count / total_subs) * 100 if total_subs > 0 else 0
                    st.metric("Paid Subscribers", f"{paid_count:,}")
                else:
                    st.metric("Paid Subscribers", "N/A")

            with m3:
                if 'active_subscription' in subs_df.columns:
                     st.metric("Free to Paid Conversion", f"{conversion_rate:.2f}%")
                else:
                    st.metric("Free to Paid Conversion", "N/A")
            
            # --- Growth Chart ---
            st.divider()
            st.subheader("Subscriber Growth & Catalysts")
            
            # Prepare daily data
            daily_growth = subs_df.set_index('created_at').resample('D').size().cumsum().reset_index(name='total_subscribers')
            
            fig = px.line(
                daily_growth, 
                x='created_at', 
                y='total_subscribers',
                template="plotly_dark",
                labels={'created_at': 'Date', 'total_subscribers': 'Subscribers'}
            )
            
            fig.update_traces(line_color='#FF4B4B', line_width=3)
            
            # Catalyst Overlay
            if posts_df is not None and not posts_df.empty:
                 # Match posts to timestamps
                relevant_posts = posts_df[
                    (posts_df['post_date'] >= daily_growth['created_at'].min()) &
                    (posts_df['post_date'] <= daily_growth['created_at'].max())
                ].copy()
                
                if not relevant_posts.empty:
                    # Sort for merge_asof
                    relevant_posts = relevant_posts.sort_values('post_date')
                    daily_growth = daily_growth.sort_values('created_at')
                    
                    # Find y-values for markers
                    posts_integrated = pd.merge_asof(
                        relevant_posts, 
                        daily_growth, 
                        left_on='post_date', 
                        right_on='created_at', 
                        direction='nearest'
                    )
                    
                    fig.add_trace(go.Scatter(
                        x=posts_integrated['post_date'],
                        y=posts_integrated['total_subscribers'],
                        mode='markers',
                        name='Published Post',
                        text=posts_integrated['title'],
                        hovertemplate='<b>%{text}</b><br>%{x|%Y-%m-%d}<extra></extra>',
                        marker=dict(color='#00FF00', size=10, symbol='x', line=dict(width=2, color='white'))
                    ))

            fig.update_layout(
                xaxis_title="",
                yaxis_title="",
                hovermode="x unified",
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="right",
                    x=1
                ),
                margin=dict(l=0, r=0, t=30, b=0)
            )
            
            st.plotly_chart(fig, use_container_width=True)

            # --- Spike Analysis ---
            st.divider()
            st.subheader("🚀 Highest Growth Spike")
            
            # Calculate 3-day pct change
            daily_growth['pct_change_3d'] = daily_growth['total_subscribers'].pct_change(periods=3)
            
            # Find max
            if not daily_growth['pct_change_3d'].isna().all():
                max_idx = daily_growth['pct_change_3d'].idxmax()
                max_val = daily_growth.loc[max_idx, 'pct_change_3d']
                peak_date = daily_growth.loc[max_idx, 'created_at']
                
                window_start = peak_date - pd.Timedelta(days=3)
                
                col1, col2 = st.columns([1, 2])
                
                with col1:
                    st.info(f"**Top Window:**\n\n{window_start.strftime('%b %d')} - {peak_date.strftime('%b %d')}")
                    st.metric("Growth in 3 Days", f"{max_val:.1%}")

                with col2:
                    st.write("Does this align with a specific post?")
                    # Find catalyst
                    if posts_df is not None:
                        # Look for posts in the 3-5 days BEFORE the window start or during the window
                        # Broaden search slightly: posts published just before or during the spike
                        search_end = peak_date
                        search_start = window_start - pd.Timedelta(days=2) 
                        
                        catalysts = posts_df[
                            (posts_df['post_date'] >= search_start) & 
                            (posts_df['post_date'] <= search_end)
                        ]
                        
                        if not catalysts.empty:
                            st.success(f"**Yes! {len(catalysts)} post(s) found near this spike:**")
                            for _, post in catalysts.iterrows():
                                st.markdown(f"📄 **{post['title']}**")
                                st.caption(f"Published: {post['post_date'].strftime('%Y-%m-%d')} • {post['type'].title() if 'type' in post else 'Post'}")
                        else:
                            st.warning("No specific post found immediately preceding this spike. Could be external traffic?")
            else:
                 st.write("Not enough data to calculate growth spikes.")

    else:
        st.error("Could not find necessary CSV files (`posts.csv` and `subscribers.csv` or `email_list.csv`) in the zip archive.")
else:
    # Empty State
    st.info("👆 Upload your zip file to get started.")

