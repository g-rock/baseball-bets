### Command to deploy

```
gcloud functions deploy get_top_vs_bottom_matchups \
    --runtime python311 \
    --trigger-http \
    --allow-unauthenticated
```