from django.test import TestCase

from posts.models import Post


class PostListTests(TestCase):
    def test_only_published_posts_are_listed(self):
        Post.objects.create(title="Draft", body="...", published=False)
        Post.objects.create(title="Live", body="...", published=True)
        response = self.client.get("/api/posts/")
        self.assertEqual([p["title"] for p in response.json()["results"]], ["Live"])

    def test_missing_post_is_404(self):
        self.assertEqual(self.client.get("/api/posts/999/").status_code, 404)
