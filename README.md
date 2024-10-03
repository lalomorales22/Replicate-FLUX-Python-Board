# Replicate Flux Message Board

This project is a rad message board application that allows users to post messages, generate images using the Replicate API with the Flux model, comment on posts, and react to messages. It's built with Flask and uses Socket.IO for real-time updates.

GitHub Repository: [https://github.com/lalomorales22/Replicate-Flux-Message-Board.git](https://github.com/lalomorales22/Replicate-Flux-Message-Board.git)

## Features

- User authentication (register, login, logout)
- Post messages with optional image generation
- Generate images using Replicate API with Flux model
- Comment on messages
- React to messages with emojis
- Tag messages and browse by tags
- Real-time updates using Socket.IO
- User profiles

## Requirements

- Python 3.7+
- Flask
- Flask-SocketIO
- Flask-Login
- Pillow
- requests
- replicate
- python-dotenv

## Installation

1. Clone the repository:
   ```
   git clone https://github.com/lalomorales22/Replicate-Flux-Message-Board.git
   cd Replicate-Flux-Message-Board
   ```

2. Create a virtual environment and activate it:
   ```
   python -m venv venv
   source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
   ```

3. Install the required packages:
   ```
   pip install -r requirements.txt
   ```

4. Set up your Replicate API token:
   - Sign up for a Replicate account and get your API token
   - Create a `.env` file in the project root and add your token:
     ```
     REPLICATE_API_TOKEN=your_api_token_here
     ```

## Usage

1. Run the application:
   ```
   python app.py
   ```

2. Open a web browser and navigate to `http://localhost:5000`

3. Register a new account or log in if you already have one

4. Start posting messages, generating images, and interacting with other users!

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is open source and available under the [MIT License](LICENSE).

