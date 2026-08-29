const questions = [
  "오늘 딱 하나 바꿀 수 있다면 무엇일까요?",
  "요즘 자꾸 마음이 가는 것은 무엇인가요?",
  "완벽하지 않아도 시작해 볼 수 있는 일은?",
  "오늘의 나에게 가장 필요한 한마디는 무엇일까요?",
  "이번 주에 새롭게 배워 보고 싶은 것은?",
  "누군가에게 고맙다고 말한다면 누구인가요?",
];

const idea = document.querySelector("#idea");
const ideaButton = document.querySelector("#ideaButton");
const year = document.querySelector("#year");
let currentQuestion = 0;

year.textContent = new Date().getFullYear();

ideaButton.addEventListener("click", () => {
  currentQuestion = (currentQuestion + 1) % questions.length;
  idea.textContent = questions[currentQuestion];
  idea.classList.remove("is-changing");
  void idea.offsetWidth;
  idea.classList.add("is-changing");
});
