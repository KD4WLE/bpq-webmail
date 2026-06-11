from pathlib import Path

p = Path("templates/bulletins.html")
text = p.read_text()

old = '''<form method="get" action="/bulletins" class="filter-form">
  <input type="text" name="q" value="{{ q }}" placeholder="Search subject, sender, category, or area">
  <input type="hidden" name="category" value="{{ category }}">
  <button type="submit">Search</button>
  <a class="button" href="/bulletins">Clear</a>
  <a class="button" href="/bulletins?refresh=1">Refresh</a>
</form>

<div class="category-pills">
  <a class="pill {% if not category %}active{% endif %}" href="/bulletins">All</a>
  {% for c in categories %}
    <a class="pill {% if category == c %}active{% endif %}" href="/bulletins?category={{ c }}{% if q %}&q={{ q }}{% endif %}">
      {{ c }}
    </a>
  {% endfor %}
</div>'''

new = '''<form method="get" action="/bulletins" class="filter-form">
  <input type="text" name="q" value="{{ q }}" placeholder="Search subject, sender, category, or area">

  <select name="category">
    <option value="">All Categories</option>
    {% for c in categories %}
      <option value="{{ c }}" {% if category == c %}selected{% endif %}>{{ c }}</option>
    {% endfor %}
  </select>

  <button type="submit">Apply</button>
  <a class="button" href="/bulletins">Clear</a>
  <a class="button" href="/bulletins?refresh=1">Refresh</a>
</form>'''

if old not in text:
    raise SystemExit("Expected category pills block not found")

p.write_text(text.replace(old, new, 1))
print("Converted bulletin category pills to dropdown")
