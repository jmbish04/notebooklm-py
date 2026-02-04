# Docker and Cloudflare Workers Deployment

This guide covers running notebooklm-py in Docker containers, including integration with Cloudflare Workers.

## Docker

### Quick Start

```bash
# Build the container
docker build -t notebooklm-py .

# Show CLI help
docker run -it notebooklm-py --help

# Run a command
docker run -it notebooklm-py list
```

### Interactive Login

The library requires authentication via browser. For Docker, you'll need to:

1. **Option A: Mount existing credentials**
   ```bash
   # Login on host first
   notebooklm login
   
   # Then mount credentials directory
   docker run -v ~/.notebooklm:/home/notebooklm/.notebooklm notebooklm-py list
   ```

2. **Option B: Use exported cookies**
   ```bash
   # Export cookies to JSON (see CLI reference)
   # Mount the cookie file into the container
   docker run -v ./cookies.json:/home/notebooklm/cookies.json notebooklm-py login --cookies /home/notebooklm/cookies.json
   ```

### Using Docker Compose

```bash
# Start the container
docker-compose up -d

# Run commands
docker-compose run notebooklm list
docker-compose run notebooklm create "My Notebook"

# Start server mode
docker-compose --profile server up -d notebooklm-server
```

### Persistent Storage

Credentials are stored in `/home/notebooklm/.notebooklm` inside the container. Use Docker volumes to persist authentication:

```bash
docker volume create notebooklm-data
docker run -v notebooklm-data:/home/notebooklm/.notebooklm notebooklm-py list
```

## Cloudflare Workers Container Binding

Cloudflare Workers can bind to containers, allowing you to run the Python notebooklm-py library from your Worker code.

### Prerequisites

1. Build and push the Docker image:
   ```bash
   docker build -t notebooklm-py .
   docker tag notebooklm-py registry.cloudflare.com/your-account/notebooklm-py
   docker push registry.cloudflare.com/your-account/notebooklm-py
   ```

2. Configure your Worker with container binding (see `wrangler.jsonc.example`)

### wrangler.jsonc Configuration

```jsonc
{
  "name": "notebooklm-worker",
  "main": "src/index.ts",
  "compatibility_date": "2024-01-01",
  
  "containers": {
    "NOTEBOOKLM": {
      "image": "registry.cloudflare.com/your-account/notebooklm-py:latest",
      "max_instances": 1,
      "env_vars": {
        "NOTEBOOKLM_LOG_LEVEL": "INFO"
      }
    }
  }
}
```

### Worker Code Example

```typescript
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const container = env.NOTEBOOKLM;
    
    // Example: List notebooks
    const url = new URL(request.url);
    if (url.pathname === '/notebooks') {
      // Execute notebooklm command in container
      const result = await container.exec(['notebooklm', 'list', '--json']);
      return new Response(result.stdout, {
        headers: { 'Content-Type': 'application/json' }
      });
    }
    
    return new Response('NotebookLM API', { status: 200 });
  }
};
```

### Authentication in Cloudflare

For containerized deployments, pre-configure authentication:

1. **Use environment secrets** for CSRF tokens/cookies
2. **Mount credentials** from Cloudflare secrets into the container
3. **Refresh tokens** periodically (tokens expire after ~24 hours)

See [Configuration](configuration.md) for credential storage details.

## References

- [Cloudflare Container Bindings](https://developers.cloudflare.com/containers/get-started)
- [Cloudflare Container Package](https://developers.cloudflare.com/containers/container-package)
- [Configuration Guide](configuration.md)
- [Troubleshooting](troubleshooting.md)
