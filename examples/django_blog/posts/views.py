from django.http import Http404, JsonResponse

from posts.models import Post


def post_list(request):
    posts = Post.objects.filter(published=True).values("id", "title")
    return JsonResponse({"results": list(posts)})


def post_detail(request, pk):
    try:
        post = Post.objects.get(pk=pk, published=True)
    except Post.DoesNotExist:
        raise Http404("Post not found")
    return JsonResponse({"id": post.pk, "title": post.title, "body": post.body})
