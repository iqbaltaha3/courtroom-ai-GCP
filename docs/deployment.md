# Automatic deployment

[GitHub Actions runs](https://github.com/iqbaltaha3/courtroom-ai-GCP/actions/workflows/deploy.yml)

Every push to `main` runs backend lint and tests, TypeScript checks, and a frontend build. Successful checks trigger container builds and deployment of both existing Cloud Run services in `courtroom-ai-509709`, region `asia-south1`. Pull requests run checks only. The workflow also supports manual runs from the Actions tab on `main`.

Each image is tagged with its Git commit SHA in the existing `cloud-run-source-deploy` Artifact Registry repository. The frontend API URL is supplied at build time because Vite embeds it into JavaScript. The backend image builds its research index from the committed statute CSVs and caches the embedding model.

The workflow updates container images while retaining existing Cloud Run environment variables, Secret Manager references, runtime accounts, resource limits, and public access settings. It explicitly routes traffic to the latest revision and checks backend `/health` (including statute availability) and the frontend `/` application page. The public hosting layer returns 404 for `/healthz`, so the frontend check validates the application title and React root instead. Failed CI checks prevent deployment; failed deployment steps mark the run failed. Backend and frontend updates are sequential, not atomic, and there is no automatic rollback. Production deployments are serialized.

## Authentication and permissions

GitHub Actions authenticates through Workload Identity Federation; there is no service-account JSON key in GitHub secrets. The provider restricts identity to repository ID `1434403923`, owner ID `148227111`, `refs/heads/main`, and `.github/workflows/deploy.yml` in this repository.

- Pool: `projects/558535784594/locations/global/workloadIdentityPools/github-courtroom`
- Provider: `github`
- Deployment account: `github-courtroom-deploy@courtroom-ai-509709.iam.gserviceaccount.com`
- Artifact Registry Writer on `cloud-run-source-deploy` in `asia-south1`.
- Cloud Run Developer on `courtroom-ai-backend` and `courtroom-ai-frontend`.
- Service Account User on the existing backend and frontend runtime accounts, approved during setup.

The frontend currently uses the project's default Compute service account, which already has Editor. Its deployment permission therefore trusts maintainers of `main` with that runtime identity. A dedicated frontend runtime account with minimal permissions is a future hardening step.

## Inspect or recover a deployment

Open the workflow run to inspect test output, build output, deployment status, and links. In Cloud Run, inspect the service's **Revisions** tab. To roll back, select the last known working revision and route 100% of traffic to it, or use:

```sh
gcloud run services update-traffic SERVICE \
  --project=courtroom-ai-509709 \
  --region=asia-south1 \
  --to-revisions=WORKING_REVISION=100
```

Roll back each affected service separately. The next successful workflow routes traffic to its latest deployment again.

Cloud Run's local filesystem is ephemeral. This workflow does not add durable storage or migrate case data between revisions; cases saved only inside a running container can be lost during redeployment. See the existing [deployment considerations](portfolio-interview-guide.md) before relying on stored cases across releases.

References: [Google Cloud deployment documentation](https://docs.cloud.google.com/run/docs/deploying), [Google GitHub Actions authentication](https://github.com/google-github-actions/auth), and [Workload Identity Federation for deployment pipelines](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines).
