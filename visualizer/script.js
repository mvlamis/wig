let baseImg, bangImg;
let sliderValue = 100;
const apiUrl = 'http://localhost:8000/api/score';
const fetchInterval = 100; // ms

function preload() {
    baseImg = loadImage('wig base.png');
    bangImg = loadImage('wig bang.png');
}

function setup() {
    const container = document.getElementById('p5-container');
    createCanvas(baseImg.width, baseImg.height, container);
    
    // start fetching discomfort score from Python server
    setInterval(fetchDiscomfortScore, fetchInterval);
}

function fetchDiscomfortScore() {
    fetch(apiUrl)
        .then(response => response.json())
        .then(data => {
            // update slider and value display
            const sliderInput = document.getElementById('bangSlider');
            sliderInput.value = Math.round(data.score);
            document.getElementById('slider-value').textContent = Math.round(data.score);
        })
        .catch(error => {
            console.log('cannot reach discomfort score server at', apiUrl);
        });
}

function draw() {
    image(baseImg, 0, 0);
    
    const sliderInput = document.getElementById('bangSlider');
    sliderValue = parseFloat(sliderInput.value);
    
    // how much bang image to show
    const percentageToShow = sliderValue / 100;
    const bangHeight = bangImg.height;
    const heightToShow = bangHeight * percentageToShow;
    
    image(bangImg, 0, 0, bangImg.width, heightToShow, 0, 0, bangImg.width, heightToShow);
}
