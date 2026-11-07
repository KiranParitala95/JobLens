from app import create_app

app = create_app()

if __name__ == "__main__":
    # threaded=True lets the dev server handle more than one request at a
    # time. Without it, a single slow request (e.g. the OAuth callback
    # waiting on Google's token endpoint) blocks every other request -
    # including just loading a page - until it finishes, which looked
    # like the whole app hanging.
    app.run(debug=True, threaded=True)
