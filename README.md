
### Frontend site & repository

Site: https://the-baseball-app.vercel.app/ <br>
Repository: https://github.com/rooftb/baseball-app

### Example call to deployed cloud run endpoint

```
https://us-central1-bet-baseball.cloudfunctions.net/check_firestore_and_return_response?schedule_date=2025-03-27
```    

### Deploy cloud run endpoint <sup>*</sup>
```
gcloud functions deploy check_firestore_and_return_response \
  --runtime python311 \
  --trigger-http \
  --entry-point=check_firestore_and_return_response \
  --allow-unauthenticated
```

See function in console: https://console.cloud.google.com/run?authuser=1&project=bet-baseball

<sup>*</sup>
To deploy, you must have a service account file at the root of this project `/service-account/<service-account>.json`

### Update cloud scheduler job
```
gcloud scheduler jobs update http daily-matchup-save \
  --schedule="0 1 * * *" \
  --uri="https://us-central1-bet-baseball.cloudfunctions.net/check_firestore_and_return_response?&store=true" \
  --http-method=GET \
  --time-zone="America/New_York" \
  --location=us-central1
```

See job in console: https://console.cloud.google.com/cloudscheduler?referrer=search&authuser=1&project=bet-baseball

### Run & test endpoint locally
```
ENV=LOCAL functions-framework --target=check_firestore_and_return_response --debug
```

Then visit, http://localhost:8080/check_firestore_and_return_response?schedule_date=2025-04-15 and adjust URL params