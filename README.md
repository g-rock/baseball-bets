
### Frontend site

```
https://baseball-app-five.vercel.app/
```

### Call endpoint

```
https://us-central1-bet-baseball.cloudfunctions.net/get_top_vs_bottom_matchups?schedule_date=2025-03-27&ranking_date=2024-09-01&odds_date=2025-03-27
```    

### Command to deploy

```
gcloud functions deploy get_top_vs_bottom_matchups \
    --runtime python311 \
    --trigger-http \
    --allow-unauthenticated
```