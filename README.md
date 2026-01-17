# 🧊 File Fridge

### **⚠️ CAUTION: Active Development**
**File Fridge is currently under active development.** While we strive for stability, updates may occasionally introduce breaking changes. Please ensure you have backups of your data and database before updating. Use at your own risk in production environments.

---

## What is File Fridge?

**File Fridge** is an intelligent storage management tool that helps you save money and space on your servers.

Do you have expensive, high-speed hard drives (Hot Storage) filling up with files you haven't looked at in months? Instead of buying more expensive drives, File Fridge automatically identifies those "cold" files and moves them to cheaper, high-capacity storage (Cold Storage).

Think of it as a smart assistant that keeps your desk (Hot Storage) clean by moving old paperwork into the filing cabinet (Cold Storage) for you.

## Why use File Fridge?

*   **💰 Save Money**: Keep your expensive SSDs or high-speed drives free for the files you actually use.
*   **🤖 Automation**: Set your rules once and let File Fridge handle the cleanup on a schedule.
*   **🔗 Seamless Access**: Use the "Symlink" feature to move files while keeping them visible in their original location.
*   **📊 Insights**: See exactly how much space you've saved and track your storage trends.
*   **🛡️ Stay Organized**: Use tags and automated rules to categorize your data across all storage locations.

## Getting Started

Getting File Fridge up and running is simple, especially if you use Docker.

1.  **Install**: Follow our [Installation Guide](docs/INSTALLATION.md) to get the application running.
2.  **Connect Storage**: Tell File Fridge where your "Hot" and "Cold" storage locations are.
3.  **Set Your Rules**: Decide which files should stay "Hot" (e.g., "Keep files accessed in the last 30 days").
4.  **Relax**: File Fridge will scan your files and move them to cold storage automatically.

## Detailed Documentation

For technical users, homelab enthusiasts, and enterprises, we have detailed guides available in the `docs/` directory:

*   **[Installation Guide](docs/INSTALLATION.md)**: Detailed steps for Docker and manual installations.
*   **[Usage Guide](docs/USAGE.md)**: How to get started with monitored paths and criteria.
*   **[Update Guide](docs/UPDATES.md)**: How to keep File Fridge up to date and our versioning strategy.
*   **[Feature Rundown](docs/FEATURES.md)**: A complete list of everything File Fridge can do.
*   **[Docker Deployment](docs/DOCKER.md)**: Advanced Docker configuration and symlink handling.
*   **[Usage & Best Practices](docs/CONFIGURATION_GUIDE.md)**: How to get the most out of your scan intervals and criteria.
*   **[Notifications](docs/NOTIFICATIONS.md)**: Setting up Email and Webhook alerts.
*   **[Tagging & Rules](docs/TAGGING.md)**: Organizing your files automatically.
*   **[REST API](docs/API.md)**: Information for developers and automation.

---

## License

Apache License 2.0 - See [LICENSE](LICENSE) file for details.

## Support

For issues, questions, or feature requests, please open an issue on the project repository.
