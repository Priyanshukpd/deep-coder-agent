from flask import Flask, render_template_string

app = Flask(__name__)

@app.route('/')
def home():
    # Render a blue submit button with white text
    button_html = "<button style='background:blue; color:white;'>Submit</button>"
    return render_template_string(button_html)

if __name__ == '__main__':
    app.run(debug=True)
