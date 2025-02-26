using Azure.Storage.Blobs;
using Azure.Storage.Blobs.Models;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Azure.Functions.Worker;
using Microsoft.Extensions.Logging;

namespace MoreHavoc.Proxy
{
    public class BasemapProxy
    {
        private static readonly Random Random = new Random();
        private readonly ILogger<BasemapProxy> _logger;

        private static readonly string StorageConnectionString = Environment.GetEnvironmentVariable("AzureWebJobsStorage") ?? "UseDevelopmentStorage=true";
        private static readonly string ContainerName = Environment.GetEnvironmentVariable("STORAGE_CONTAINER_NAME");
        private readonly BlobServiceClient _blobServiceClient;
        private readonly BlobContainerClient _containerClient;

        public BasemapProxy(ILogger<BasemapProxy> logger)
        {
            _logger = logger;
            _blobServiceClient = new BlobServiceClient(StorageConnectionString);
            _containerClient = _blobServiceClient.GetBlobContainerClient(ContainerName);
        }

        [Function("BasemapProxy")]
        public async Task<IActionResult> Run(
            [HttpTrigger(AuthorizationLevel.Anonymous, "get", Route = "{region}/{*path}")] HttpRequest req,
            string region,
            string path)
        {
            try
            {
                // List all "test" directories in the region
                var parentDirectories = _containerClient.GetBlobsByHierarchy(prefix: $"{region}/", delimiter: "/")
                    .Where(p => p.Prefix.Split('/')[1].EndsWith(".vtiles", StringComparison.OrdinalIgnoreCase))
                    .Select(p => p.Prefix)
                    .ToArray();

                if (!parentDirectories.Any())
                {
                    _logger.LogWarning($"No test directories found in region: {region}");
                    return new NotFoundResult();
                }

                // Randomly select one of the parent directories
                string selectedParentDir = parentDirectories[Random.Next(parentDirectories.Length)];

                // Construct the full blob path
                string blobPath = $"{selectedParentDir}{path}";

                // Get the blob
                BlobClient blobClient = _containerClient.GetBlobClient(blobPath);

                // Check if blob exists
                if (!await blobClient.ExistsAsync())
                {
                    // If the blob doesn't exist, check if it's a directory request
                    // by looking for root.json in the same path
                    string rootJsonPath = blobPath.TrimEnd('/') + "/root.json";
                    BlobClient rootJsonClient = _containerClient.GetBlobClient(rootJsonPath);

                    if (!await rootJsonClient.ExistsAsync())
                    {
                        _logger.LogWarning($"Neither blob nor root.json found: {blobPath}");
                        return new NotFoundResult();
                    }

                    // Use the root.json file instead
                    blobClient = rootJsonClient;
                }

                // Get blob properties to determine content type
                BlobProperties properties = await blobClient.GetPropertiesAsync();

                // Download the blob
                MemoryStream memoryStream = new MemoryStream();
                await blobClient.DownloadToAsync(memoryStream);
                memoryStream.Seek(0, SeekOrigin.Begin);

                // Return the file with the content type from blob properties
                return new FileStreamResult(memoryStream, GetContentType(Path.GetExtension(blobClient.Name), properties.ContentType))
                {
                    EnableRangeProcessing = true
                };
            }
            catch (Exception ex)
            {
                _logger.LogError($"Error processing request: {ex}");
                return new StatusCodeResult(500);
            }
        }

        private static string GetContentType(string extension, string defaultContentType)
        {
            return extension.ToLower() switch
            {
                ".pbf" => "application/x-protobuf",
                ".json" => "application/json",
                _ => defaultContentType
            };
        }
    }
}